"""自动调律工作流 — 滚动流程测试"""

from enum import Enum

from loguru import logger

from src.workflows.base import BaseWorkflow


class ScrollState(Enum):
    """滚动校验结果"""
    BOTTOM = "bottom"       # 到底，结束
    PROCESS = "process"     # 有新行待处理
    SKIP = "skip"           # 本轮无新行，跳过


class AutoTuningWorkflow(BaseWorkflow):
    """自动调律工作流"""

    WEAPON_SLOTS = ["main_weapon", "sub_weapon", "ring", "pendant"]
    ARMOR_SLOTS = ["head", "chest", "leg", "wrist"]
    WEAPON_DETAIL = "equip_weapon_detail"
    ARMOR_DETAIL = "equip_armor_detail"

    SCAN_FIELDS = [
        "equip_type", "equip_level", "base_attr",
        "affix_gong", "affix_shang", "affix_jue",
        "affix_zhi", "affix_yu",
    ]

    GRID_SCENE = "bag_equip_detail"
    GRID_PANEL = "bag_grid"

    def run(self) -> dict:
        self._navigate_to_equip()

        selected = getattr(self, '_selected_slots', None)
        if not selected:
            selected = self.WEAPON_SLOTS + self.ARMOR_SLOTS

        for slot in selected:
            if self.is_stopped:
                break
            if slot in self.WEAPON_SLOTS:
                self._process_slot_group(slot, self.WEAPON_DETAIL)
            elif slot in self.ARMOR_SLOTS:
                self._process_slot_group(slot, self.ARMOR_DETAIL)

        self._navigate_back()
        logger.info("自动调律完成")
        return self.output

    # ─── 导航 ──────────────────────────────────────────────

    def _navigate_to_equip(self):
        result = self.ocr_scene("game_menu_page", ["wulinlu"])
        if result.get("wulinlu") != "武林录":
            self.click_region("game_main_page", "menu")
            self.wait_delay("step_interval")
        else:
            logger.info("当前在菜单页")

        self.click_region("game_menu_page", "bag")
        self.wait_delay("step_interval")

        bag_scan = self.ocr_scene("bag_equip_detail", ["sub_equip"])
        if "装备" not in bag_scan.get("sub_equip", ""):
            self.click_region("bag_equip_detail", "training")
            self.wait_delay("step_interval")

    def _navigate_back(self):
        self.click_region("bag_equip_detail", "back")
        self.wait_delay("step_interval")
        self.click_region("game_menu_page", "back")

    # ─── 部位处理 ──────────────────────────────────────────

    def _process_slot_group(self, slot: str, detail_scene: str):
        logger.info(f"── 处理部位: {slot} ──")
        self.click_region(self.GRID_SCENE, slot)
        self.wait_delay("step_interval")
        self._traverse_bag(detail_scene)

    # ─── 背包遍历（按 tuning-mechanics.md 设计）──────────────

    def _read_row(self, detail_scene: str, row: int) -> tuple[str, str]:
        """点击指定行第1列，等面板刷新后 OCR，返回 (装备名, 指纹)"""
        self.click_panel(self.GRID_SCENE, self.GRID_PANEL, row, 1)
        self.wait_delay("page_refresh_wait")
        raw = self.ocr_scene(detail_scene, self.SCAN_FIELDS)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        name = equip.get("equip_type", "").split("|")[-1].strip() if equip else ""
        fp = self._make_fingerprint(equip)
        return name, fp

    def _process_new_rows(self, detail_scene: str, fps: list[str],
                          row_index: int, first_real_row: int,
                          real_rows: int) -> int:
        """处理当前可见区中 row_index 之后的新行，返回更新后的 row_index。

        grid 第1行 = 逻辑行 first_real_row，win_row = logical_row - first_real_row + 1。
        遇到空行视为到底，立即停止（不 append 空串占位）。
        返回值 == 传入 row_index 表示没有新行被处理（调用方据此判断结束）。
        """
        last_real_row = first_real_row + real_rows - 1
        processed = row_index
        for logical_row in range(row_index + 1, last_real_row + 1):
            win_row = logical_row - first_real_row + 1
            name, fp = self._read_row(detail_scene, win_row)
            if not fp:
                logger.info(f"  新行{logical_row} grid[{win_row}][1] 空 slot → 到底")
                break
            fps.append(fp)
            logger.info(f"  新行{logical_row} grid[{win_row}][1] {name} fp={fp}")
            processed = logical_row
        return processed

    def _scroll_and_verify_step(self, detail_scene: str, fps: list[str],
                                first_real_row: int) -> tuple[ScrollState, int]:
        """滚动一行 + 三向校验 + 小步修正，返回 (状态, 当前可见行数)。

        严格按 tuning-mechanics.md 设计，不加任何额外逻辑。
        正常步进：返回 PROCESS。
        连续 4 次滚少了：返回 BOTTOM（到底）。
        连续 4 次滚多了：抛 RuntimeError（不应发生）。
        未知 nfp / 越界：抛异常，暴露问题。
        """
        # 大幅步进（1 行）
        self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", hold=0.3)
        self.wait_delay("scroll_settle_wait")
        alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)

        short_count = 0
        long_count = 0
        while not self.is_stopped:
            _, nfp = self._read_row(detail_scene, 1)

            # 空指纹 = 空行 = 到底（每行第一列不可能为空，空即超出最后一行）
            if not nfp:
                logger.info("  空指纹 → 空行 → 到底")
                return ScrollState.BOTTOM, alignment.n_rows

            if nfp == fps[first_real_row]:
                logger.info(f"  正常步进: nfp={nfp} == fps[{first_real_row}]")
                return ScrollState.PROCESS, alignment.n_rows
            elif nfp == fps[first_real_row - 1]:
                short_count += 1
                logger.info(f"  滚少了({short_count}/4): nfp={nfp} == fps[{first_real_row - 1}]，补滚 0.25")
                if short_count >= 4:
                    logger.info("连续4次滚少了 → 到底")
                    return ScrollState.BOTTOM, alignment.n_rows
                self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", distance=0.25)
                self.wait_delay("scroll_settle_wait")
                alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            elif nfp == fps[first_real_row + 1]:
                long_count += 1
                logger.info(f"  滚多了({long_count}/4): nfp={nfp} == fps[{first_real_row + 1}]，回滚 0.25")
                if long_count >= 4:
                    raise RuntimeError(
                        f"连续4次判定滚多了，不应发生："
                        f"nfp={nfp} first_real_row={first_real_row} fps={fps}"
                    )
                self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "down", distance=0.25)
                self.wait_delay("scroll_settle_wait")
                alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            else:
                raise RuntimeError(
                    f"未知滚动状态：nfp={nfp} first_real_row={first_real_row} fps={fps}"
                )
        return ScrollState.SKIP, 0  # is_stopped 中断

    def _traverse_bag(self, detail_scene: str):
        """背包滚动遍历（严格按 tuning-mechanics.md 设计）

        状态变量：
            row_index      = 已处理过的行数
            first_real_row = 当前可见区第一行的逻辑行号（从1起）
            last_real_row  = 当前实际出现的最后一行逻辑行号
            fps            = 各逻辑行首列指纹（0 起始索引）
        """
        # 从 panel 定义获取初始行列数
        panel_obj = self._find_panel(self.GRID_SCENE, self.GRID_PANEL)
        if panel_obj is None:
            logger.error(f"未找到 panel {self.GRID_SCENE}.{self.GRID_PANEL}")
            return
        initial_rows = panel_obj.rows
        cols = panel_obj.cols
        logger.info(f"背包遍历开始: 初始行数={initial_rows}, 列数={cols}")

        fps: list[str] = []

        # 初始扫描 = 处理第一批新行（row_index=0, first_real_row=1, real_rows=initial_rows）
        self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
        logger.info("═══ 初始扫描 ═══")
        row_index = self._process_new_rows(detail_scene, fps, 0, 1, initial_rows)
        first_real_row = 1
        logger.info(f"初始完成: row_index={row_index} first_real_row=1 fps={fps}")
        if row_index == 0:
            logger.info("初始扫描即到底，结束")
            return

        # 主循环：滚动 → 校验 → 处理新行
        last_real_rows = 0
        scroll_round = 0
        while not self.is_stopped:
            scroll_round += 1
            logger.info(f"═══ 滚动 #{scroll_round} ═══")

            state, real_rows = self._scroll_and_verify_step(
                detail_scene, fps, first_real_row)

            if state == ScrollState.BOTTOM:
                last_real_rows = real_rows
                break
            if state == ScrollState.SKIP:
                continue

            # PROCESS：正常步进，first_real_row +1
            first_real_row += 1
            last_real_row = first_real_row + real_rows - 1
            logger.info(
                f"步进确认: first_real_row={first_real_row} "
                f"real_rows={real_rows} last_real_row={last_real_row} row_index={row_index}"
            )

            if row_index == last_real_row:
                logger.info("  无新行，本轮不处理")
                continue

            row_index = self._process_new_rows(
                detail_scene, fps, row_index, first_real_row, real_rows)
            logger.info(f"  row_index → {row_index}, fps={fps}")

        # 循环结束：处理剩余可见行（到底时处理，正常退出时 row_index==last_real_row 不处理）
        if last_real_rows > 0:
            final_last = first_real_row + last_real_rows - 1
            if row_index < final_last:
                row_index = self._process_new_rows(
                    detail_scene, fps, row_index, first_real_row, last_real_rows)
                logger.info(f"  剩余行处理完毕 row_index → {row_index}, fps={fps}")


    # ─── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _make_fingerprint(equip: dict) -> str:
        """生成装备指纹，空装备返回空串。

        空装备判定：type/name/level 全为空或 None。
        """
        if not isinstance(equip, dict) or not equip:
            return ""
        # 检查是否有有效装备数据（type/level 至少有一个非空）
        equip_type = equip.get("type") or ""
        equip_name = equip.get("name") or ""
        equip_level = equip.get("level") or ""
        if not equip_type and not equip_name and not equip_level:
            return ""  # 空装备 → 空指纹
        import hashlib
        parts = [
            str(equip_type),
            str(equip_level),
            str(equip.get("quality", "") or ""),
            str(equip.get("chengyin", "") or ""),
        ]
        for i in range(1, 6):
            affix = equip.get(f"affix_{i}")
            if isinstance(affix, dict) and affix.get("name"):
                parts.append(f"{affix['name']}:{affix.get('value', '')}")
        raw = "+".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()[:8]
