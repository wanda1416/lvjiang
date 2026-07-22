"""自动调律工作流 — 代码实现

遍历 8 个装备部位，对每个部位的背包 grid 进行滚动遍历，
对每件装备执行调律操作（当前为 mock，后续接入实际调律逻辑）。
"""

from loguru import logger

from ..base import BaseWorkflow


class AutoTuningWorkflow(BaseWorkflow):
    """自动调律工作流

    流程：主界面 → 菜单 → 包裹 → 装备页 → 遍历 8 部位 → 返回
    """

    # ── 部位分组 ──
    WEAPON_SLOTS = ["main_weapon", "sub_weapon", "ring", "pendant"]
    ARMOR_SLOTS = ["head", "chest", "leg", "wrist"]
    WEAPON_DETAIL = "equip_weapon_detail"
    ARMOR_DETAIL = "equip_armor_detail"

    # ── 扫描字段 ──
    SCAN_FIELDS = [
        "equip_type", "equip_level", "base_attr",
        "affix_gong", "affix_shang", "affix_jue",
        "affix_zhi", "affix_yu",
    ]

    # ── grid 参数 ──
    GRID_SCENE = "bag_equip_detail"
    GRID_PANEL = "bag_grid"
    INITIAL_ROWS = 3
    COLS = 6

    def run(self) -> dict:
        """主流程

        通过 _selected_slots 属性指定要处理的部位（由对话框设置），
        若未设置则默认处理全部 8 个部位。
        """
        self._navigate_to_equip()

        # 获取要处理的部位列表
        selected = getattr(self, '_selected_slots', None)
        if not selected:
            selected = self.WEAPON_SLOTS + self.ARMOR_SLOTS

        # 按部位类型分组处理
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
        """从主界面导航到装备培养页"""
        # 检查是否已在菜单页
        result = self.ocr_scene("game_menu_page", ["wulinlu"])
        if result.get("wulinlu") != "武林录":
            self.click_region("game_main_page", "menu")
            self.wait_delay("step_interval")
        else:
            logger.info("当前在菜单页")

        # 进入包裹
        self.click_region("game_menu_page", "bag")
        self.wait_delay("step_interval")

        # 确认在装备页（可能停在道具页等其他页面）
        bag_scan = self.ocr_scene("bag_equip_detail", ["sub_equip"])
        if "装备" not in bag_scan.get("sub_equip", ""):
            self.click_region("bag_equip_detail", "training")
            self.wait_delay("step_interval")

    def _navigate_back(self):
        """从装备页返回主界面"""
        self.click_region("bag_equip_detail", "back")
        self.wait_delay("step_interval")
        self.click_region("game_menu_page", "back")

    # ─── 部位处理 ──────────────────────────────────────────

    def _process_slot_group(self, slot: str, detail_scene: str):
        """点击某装备部位 → 遍历该部位所有装备"""
        logger.info(f"── 处理部位: {slot} ({detail_scene}) ──")
        self.click_region(self.GRID_SCENE, slot)
        self.wait_delay("step_interval")
        self._traverse_bag(detail_scene)

    # ─── 背包遍历（核心滚动逻辑）──────────────────────────

    def _traverse_bag(self, detail_scene: str):
        """单部位 grid 遍历：初始 3 行 + 滚动循环

        滚动状态机：
            drag 后读 grid[1][1]：
            - 空          → 到底，结束
            - == fp_r1    → 内容未移动（grid 撑满），到底，结束
            - == fp_r2    → 滚动成功，验证 row 3 可见后处理
            - 其他        → 滚动过头，微调修正
        """
        fingerprints: dict[str, str] = {}

        # 初始处理可见 3 行
        self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
        for r in range(1, self.INITIAL_ROWS + 1):
            for c in range(1, self.COLS + 1):
                self._process_slot(r, c, detail_scene, fingerprints)

        # 记录行首指纹（col=1）
        fp_r1 = fingerprints.get("r1c1", "")
        fp_r2 = fingerprints.get("r2c1", "")
        fp_r3 = fingerprints.get("r3c1", "")

        # ── 滚动循环 ──
        while not self.is_stopped:
            # 滚动一行
            self.drag_grid(self.GRID_SCENE, self.GRID_PANEL,
                           2, 3, "up", hold=0.3)
            self.wait_delay("step_interval")

            # 对齐 + 读 grid[1][1]
            self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            check_equip = self.scan_panel(
                self.GRID_SCENE, self.GRID_PANEL, 1, 1,
                detail_scene, self.SCAN_FIELDS)
            check_fp = self._make_fingerprint(check_equip)

            # ── 到底检测 ──
            # 1) grid[1][1] 为空 → 绝对到底
            if not check_fp:
                logger.info("该部位遍历结束（到底：grid[1][1] 为空）")
                return

            # 2) grid[1][1] == fp_r1 → 内容未移动 → 到底
            #    选定装备后 grid 无空行，内容撑满时 drag 完全无效
            if check_fp == fp_r1:
                logger.info("该部位遍历结束（到底：内容未移动）")
                return

            # 3) 滚动校验（处理过头情况）
            if not self._verify_scroll(check_fp, fp_r1, fp_r2, detail_scene):
                logger.info("该部位遍历结束（校验失败）")
                return

            # ── 确保 row 3 可见（处理部分滚动 0.8 行的情况）──
            row3_ok, row3_fp = self._ensure_row3(detail_scene)
            if not row3_ok:
                logger.info("该部位遍历结束（到底：补滚后 row 3 仍无内容）")
                return

            # 滑动指纹窗口
            fp_r1, fp_r2 = fp_r2, fp_r3

            # 处理新行（row 3）
            has_new = False
            last_fp = row3_fp
            for c in range(1, self.COLS + 1):
                slot_key = f"r3c{c}"
                # col=1 已在 _ensure_row3 中读过，直接复用
                if c == 1:
                    fp = row3_fp
                else:
                    equip = self.scan_panel(
                        self.GRID_SCENE, self.GRID_PANEL, 3, c,
                        detail_scene, self.SCAN_FIELDS)
                    fp = self._make_fingerprint(equip)
                if fp and fingerprints.get(slot_key) != fp:
                    has_new = True
                    fingerprints[slot_key] = fp
                    self._tune_equip(equip, 3, c)
                last_fp = fp

            fp_r3 = last_fp

            if not has_new:
                logger.info("该部位遍历结束（无新内容）")
                return

    def _verify_scroll(self, check_fp: str, fp_r1: str, fp_r2: str,
                       detail_scene: str, max_retry: int = 4) -> bool:
        """校验滚动是否过头（fp_r1 和空值已在调用方处理）

        Returns:
            True = check_fp 已确认为 fp_r2（滚动正确）
            False = 校验失败
        """
        for attempt in range(max_retry):
            if check_fp == fp_r2:
                return True  # 滚动正确：旧 row2 → 新 row1

            # 过头：grid[1][1] 不是 fp_r1 也不是 fp_r2 → 滚多了
            logger.debug(f"滚动过头 (attempt {attempt + 1})，回调")
            self.drag_grid(self.GRID_SCENE, self.GRID_PANEL,
                           2, 3, "down", distance=0.2)
            self.wait_delay("step_interval")

            # 重新读取
            self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            equip = self.scan_panel(
                self.GRID_SCENE, self.GRID_PANEL, 1, 1,
                detail_scene, self.SCAN_FIELDS)
            check_fp = self._make_fingerprint(equip)

            if not check_fp:
                return False  # 到底

        logger.warning("滚动校验失败：超过最大重试次数")
        return False

    def _ensure_row3(self, detail_scene: str,
                     max_nudge: int = 3) -> tuple[bool, str]:
        """确保 grid[3][1] 有内容（处理部分滚动 0.8 行的情况）

        部分滚动时 row 1-2 已正确（fp_r2/fp_r3），但 row 3 尚未完全进入可视区。
        通过小步补滚使 row 3 可见。

        Returns:
            (True, fp)  — row 3 有内容，fp 为 grid[3][1] 的指纹
            (False, "") — 补滚后仍无内容，判定到底
        """
        for _ in range(max_nudge):
            equip = self.scan_panel(
                self.GRID_SCENE, self.GRID_PANEL, 3, 1,
                detail_scene, self.SCAN_FIELDS)
            fp = self._make_fingerprint(equip)
            if fp:
                return True, fp  # row 3 有内容

            # 小步补滚
            self.drag_grid(self.GRID_SCENE, self.GRID_PANEL,
                           2, 3, "up", distance=0.3)
            self.wait_delay("step_interval")
            self.align_panel(self.GRID_SCENE, self.GRID_PANEL)

        return False, ""  # 补滚失败，到底

    def _process_slot(self, row: int, col: int, detail_scene: str,
                      fingerprints: dict[str, str]):
        """处理单个格子：点击 → 扫描 → 指纹去重 → 调律，重复直到空位"""
        while not self.is_stopped:
            self.click_panel(self.GRID_SCENE, self.GRID_PANEL, row, col)
            equip = self.scan_panel(
                self.GRID_SCENE, self.GRID_PANEL, row, col,
                detail_scene, self.SCAN_FIELDS)

            # 空位（type 为空）
            if not equip or not equip.get("type"):
                return

            fp = self._make_fingerprint(equip)
            slot_key = f"r{row}c{col}"

            # 已处理过（指纹重复）
            if fingerprints.get(slot_key) == fp:
                return

            fingerprints[slot_key] = fp
            self._tune_equip(equip, row, col)

    # ─── 调律（当前 mock）─────────────────────────────────

    def _tune_equip(self, equip: dict, row: int, col: int):
        """对装备执行调律（当前为 mock，仅日志）"""
        equip_type = equip.get("type", "?")
        level = equip.get("level", "?")
        logger.info(f"  [{row}][{col}] {equip_type} Lv.{level}")
        logger.info("装备调律完毕")

    # ─── 工具方法 ──────────────────────────────────────────

    @staticmethod
    def _make_fingerprint(equip: dict) -> str:
        """生成装备指纹（与 builtins.make_fingerprint 逻辑一致）"""
        if not isinstance(equip, dict) or not equip:
            return ""
        import hashlib
        parts = [
            str(equip.get("type", "") or ""),
            str(equip.get("level", "") or ""),
            str(equip.get("quality", "") or ""),
            str(equip.get("chengyin", "") or ""),
        ]
        for i in range(1, 6):
            affix = equip.get(f"affix_{i}")
            if isinstance(affix, dict) and affix.get("name"):
                parts.append(f"{affix['name']}:{affix.get('value', '')}")
        raw = "+".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()[:8]
