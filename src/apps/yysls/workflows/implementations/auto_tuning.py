"""自动调律工作流 — 端到端流水线

遍历背包各部位 → 对每件用潜力判定（judge_tuning_worthiness）决定是否
值得调律 → 值得的实际调律到词条满 → 每件产出结构化 report 回报给遍历
循环（output["tuning_reports"]），并为「垃圾胚子处理」与「调律后回背包页
的回收/转律/装上」预留空接口（_on_junk_blank / _on_equipment_done）。

实际调律执行逻辑（狗粮策略、进调律页、单轮调律、终局判定）自包含移植自
single_tuning，single_tuning 保持不动。
"""

from enum import Enum

from loguru import logger

from src.apps.yysls.equip_parser import EquipmentData, get_equipment_parser
from src.apps.yysls.evaluator import (
    judge_equipment_potential, judge_tuning_worthiness,
)
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

    # 调律执行相关场景/材料（移植自 single_tuning）
    TUNE_SCENE = "equip_tune_detail"
    RESULT_SCENE = "equip_tune_result"
    MATERIAL_SLOTS = [f"material_{i}" for i in range(1, 8)]
    MATERIAL_GROUP = "调律材料"
    MAX_AFFIX = 5

    def run(self) -> dict:
        self._ensure_judge_config()
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
            self.wait_delay("page_refresh_wait")  # 主界面 → 菜单页
        else:
            logger.info("当前在菜单页")

        self.click_region("game_menu_page", "bag")
        self.wait_delay("page_refresh_wait")  # 菜单页 → 背包页

        bag_scan = self.ocr_scene("bag_equip_detail", ["sub_equip"])
        if "装备" not in bag_scan.get("sub_equip", ""):
            self.click_region("bag_equip_detail", "training")
            self.wait_delay("page_refresh_wait")  # 背包页 → 调律训练页

    def _navigate_back(self):
        self.click_region("bag_equip_detail", "back")
        self.wait_delay("page_refresh_wait")  # 背包详情页 → 菜单页
        self.click_region("game_menu_page", "back")
        self.wait_delay("page_refresh_wait")  # 菜单页 → 主界面

    # ─── 部位处理 ──────────────────────────────────────────

    def _process_slot_group(self, slot: str, detail_scene: str):
        logger.info(f"── 处理部位: {slot} ──")
        self.click_region(self.GRID_SCENE, slot)
        self.wait_delay("page_refresh_wait")  # 背包浏览页 → 装备详情页
        self._traverse_bag(detail_scene)

    # ─── 背包遍历（按 tuning-mechanics.md 设计）──────────────

    @staticmethod
    def _equip_label(equip: dict) -> str:
        """日志用装备标识：name|type（缺失部分省略，全空返回'空'）"""
        if not isinstance(equip, dict) or not equip:
            return "空"
        label = "|".join(
            str(p) for p in (equip.get("name"), equip.get("type")) if p)
        return label or "空"

    def _read_row(self, detail_scene: str, row: int,
                  col: int = 1) -> tuple[str, str, dict]:
        """点击指定行/列 slot，等面板刷新后 OCR，返回 (装备名, 指纹, 装备dict)

        默认第 1 列（行指纹/滚动校验链路）；列遍历时传入 col 读非首列。"""
        self.click_panel(self.GRID_SCENE, self.GRID_PANEL, row, col)
        self.wait_delay("page_refresh_wait")
        fields = self.SCAN_FIELDS + (["base_attr_2"]
                                     if detail_scene == self.ARMOR_DETAIL else [])
        raw = self.ocr_scene(detail_scene, fields)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        name = (equip.get("name") or "") if equip else ""
        fp = self._make_fingerprint(equip)
        if fp:
            logger.debug(
                f"  读格 grid[{row}][{col}] → {self._equip_label(equip)} "
                f"lv={equip.get('level')} fp={fp}")
        else:
            logger.debug(f"  读格 grid[{row}][{col}] → 空")
        return name, fp, equip

    def _process_new_rows(self, detail_scene: str, fps: list[str],
                          row_index: int, first_real_row: int,
                          real_rows: int, cols: int) -> int:
        """处理当前可见区中 row_index 之后的新行，返回更新后的 row_index。

        grid 第1行 = 逻辑行 first_real_row，win_row = logical_row - first_real_row + 1。
        每行处理第 1..cols 全部列：第 1 列兼作行指纹（滚动校验锚点）；
        第 2 列起由 _process_row_cols 遍历。
        遇到空行视为到底，立即停止（不 append 空串占位）。
        返回值 == 传入 row_index 表示没有新行被处理（调用方据此判断结束）。
        """
        last_real_row = first_real_row + real_rows - 1
        processed = row_index
        for logical_row in range(row_index + 1, last_real_row + 1):
            win_row = logical_row - first_real_row + 1
            # ── 第 1 列：读取兼作行指纹 ──
            name, fp, equip = self._read_row(detail_scene, win_row)
            if not fp:
                logger.info(f"  新行{logical_row} grid[{win_row}][1] 空 slot → 到底")
                break
            fps.append(fp)
            logger.info(f"  新行{logical_row} grid[{win_row}][1] {name} fp={fp}")
            new_fp = self._process_equipment(name, equip, detail_scene)
            # 调律会给该行首列装备加词条 → 指纹变化；用调律后指纹覆盖。
            # 背包中该件位置不变，后续滚动再 OCR 到它时即可匹配上。
            if new_fp and new_fp != fp:
                logger.info(f"  行{logical_row} 调律后指纹更新: {fp} → {new_fp}")
                fps[-1] = new_fp
            # ── 第 2..cols 列：遍历本行剩余列 ──
            self._process_row_cols(detail_scene, win_row, logical_row, cols)
            processed = logical_row
        return processed

    def _process_row_cols(self, detail_scene: str, win_row: int,
                          logical_row: int, cols: int):
        """遍历该行第 2..cols 列装备（第 1 列由行指纹链路处理）。

        复用首列链路：click_panel grid[win_row][col] → OCR → _process_equipment。
        非首列装备不参与滚动校验，无需记录指纹（锚点仍只用首列），
        调律后指纹变化也无需回写。背包按行优先填充，空 slot 表示
        该行装备到此为止，停止本行遍历。
        """
        for col in range(2, cols + 1):
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, win_row, col)
            if not fp:
                logger.info(
                    f"  行{logical_row} grid[{win_row}][{col}] 空 slot → 本行到此为止")
                break
            logger.info(f"  行{logical_row} grid[{win_row}][{col}] {name} fp={fp}")
            self._process_equipment(name, equip, detail_scene)

    def _scroll_and_verify_step(self, detail_scene: str, fps: list[str],
                                first_real_row: int,
                                panel_rows: int) -> tuple[ScrollState, int]:
        """滚动一行 + 三向校验 + 小步修正，返回 (状态, 当前可见行数)。

        正常步进：返回 PROCESS。
        滚少了：补滚后窗口仍满行（n_rows == panel_rows，拖拽已无效果）
                → 第 2 次即确认到底；窗口非满行（真滚少了）→ 继续补滚，
                连续 3 次仍滚少则按到底收束并记录异常。
        连续 3 次滚多了：抛 RuntimeError（不应发生）。
        未知 nfp：先用第二可见行反查——若确认滚动正常仅首列 OCR 抖动，
                 则更新漂移指纹并按正常步进继续；否则抛异常暴露问题。
        """
        # 大幅步进（1 行）
        self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", hold=0.3)
        self.wait_delay("scroll_settle_wait")
        alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)

        # 对比现场：三向校验的候选指纹（next 在前沿处不存在）
        next_fp = (fps[first_real_row + 1]
                   if first_real_row + 1 < len(fps) else "无")
        logger.info(
            f"  滚动校验: first_real_row={first_real_row} len(fps)={len(fps)} "
            f"窗口行数={alignment.n_rows} 候选 prev={fps[first_real_row - 1]} "
            f"cur={fps[first_real_row]} next={next_fp}")

        short_count = 0
        long_count = 0
        while not self.is_stopped:
            _, nfp, equip1 = self._read_row(detail_scene, 1)

            # 空指纹 = 空行 = 到底（每行第一列不可能为空，空即超出最后一行）
            if not nfp:
                logger.info("  空指纹 → 空行 → 到底")
                return ScrollState.BOTTOM, alignment.n_rows

            if nfp == fps[first_real_row]:
                logger.info(f"  正常步进: nfp={nfp} == fps[{first_real_row}]")
                return ScrollState.PROCESS, alignment.n_rows
            elif nfp == fps[first_real_row - 1]:
                short_count += 1
                logger.info(
                    f"  滚少了({short_count}): nfp={nfp} == "
                    f"fps[{first_real_row - 1}] 读到={self._equip_label(equip1)} "
                    f"窗口行数={alignment.n_rows}/{panel_rows}")
                if short_count >= 2 and alignment.n_rows >= panel_rows:
                    # 补滚后窗口仍满行且首行未动 → 拖拽已无效果 = 硬到底，
                    # 无需等满 3 次，第 2 次即可确认结束
                    logger.info("  补滚后仍满行且首行未动 → 到底（提前确认）")
                    return ScrollState.BOTTOM, alignment.n_rows
                if short_count >= 3:
                    # 窗口非满行说明确实滚少了，补滚两次仍未步进不应发生：
                    # 按到底收束避免死循环，但记录异常备查
                    logger.error(
                        f"  异常：连续3次滚少了且窗口非满行"
                        f"({alignment.n_rows}/{panel_rows})，按到底收束："
                        f"nfp={nfp} first_real_row={first_real_row} fps={fps}")
                    return ScrollState.BOTTOM, alignment.n_rows
                self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", distance=0.25)
                self.wait_delay("scroll_settle_wait")
                alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            elif (first_real_row + 1 < len(fps)
                    and nfp == fps[first_real_row + 1]):
                # 边界守卫：前沿处（first_real_row 为最后一个已知行）无 next
                # 候选，直接取下标会越界；未知指纹应落入 else 诊断分支。
                long_count += 1
                logger.info(
                    f"  滚多了({long_count}/3): nfp={nfp} == "
                    f"fps[{first_real_row + 1}] 读到={self._equip_label(equip1)}，"
                    f"回滚 0.25")
                if long_count >= 3:
                    raise RuntimeError(
                        f"连续3次判定滚多了，不应发生："
                        f"nfp={nfp} first_real_row={first_real_row} fps={fps}"
                    )
                self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "down", distance=0.25)
                self.wait_delay("scroll_settle_wait")
                alignment = self.align_panel(self.GRID_SCENE, self.GRID_PANEL)
            else:
                # nfp 与三候选都不匹配：大概率是首列装备 OCR 抖动（如武器名
                # 舞鼓/舞绫鼓 截断）致指纹漂移，而非滚动出错。用第二可见行反查确认：
                # 正常步进一行时第二行 = 逻辑行 first_real_row+1，应等于 fps[...+1]。
                nfp2 = ""
                equip2: dict = {}
                if first_real_row + 1 < len(fps):
                    _, nfp2, equip2 = self._read_row(detail_scene, 2)
                if nfp2 and nfp2 == fps[first_real_row + 1]:
                    logger.warning(
                        f"  首行指纹漂移但滚动正常：nfp={nfp} ≠ "
                        f"fps[{first_real_row}]={fps[first_real_row]}，"
                        f"第二行 {nfp2} == fps[{first_real_row + 1}]，"
                        f"更新指纹并按正常步进继续"
                    )
                    fps[first_real_row] = nfp
                    return ScrollState.PROCESS, alignment.n_rows
                # 抛异常前记录完整现场，便于事后定位（如滚进非本部位区域）
                label = self._equip_label(equip1)
                logger.error(
                    f"  未知滚动状态现场: 首行={label} nfp={nfp} "
                    f"第二行={self._equip_label(equip2)} nfp2={nfp2 or '未读/空'} "
                    f"first_real_row={first_real_row} len(fps)={len(fps)} "
                    f"窗口行数={alignment.n_rows}")
                raise RuntimeError(
                    f"未知滚动状态：首行={label} nfp={nfp} "
                    f"first_real_row={first_real_row} fps={fps}"
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
        row_index = self._process_new_rows(
            detail_scene, fps, 0, 1, initial_rows, cols)
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
                detail_scene, fps, first_real_row, initial_rows)

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
                detail_scene, fps, row_index, first_real_row, real_rows, cols)
            logger.info(f"  row_index → {row_index}, fps={fps}")

        # 循环结束：处理剩余可见行（到底时处理，正常退出时 row_index==last_real_row 不处理）
        if last_real_rows > 0:
            final_last = first_real_row + last_real_rows - 1
            if row_index < final_last:
                row_index = self._process_new_rows(
                    detail_scene, fps, row_index, first_real_row, last_real_rows,
                    cols)
                logger.info(f"  剩余行处理完毕 row_index → {row_index}, fps={fps}")


    # ─── 单件装备处理（潜力判定 + 实际调律 + 回报）────────

    def _process_equipment(self, name: str, equip: dict, detail_scene: str) -> str:
        """对一件装备走完整链路：潜力判定 → 值得则调律到满 → 终局判定 → 回报。

        进入时已停在 bag_equip_detail（含 detail 区，_read_row 已点选该件）。
        未调律分支（already_full/junk_blank/no_tune_entry）保持在背包页；
        已调律分支调完后单次 back 返回背包浏览页，供遍历继续滚动。
        skip_tuning 测试开关只拦截「值得调律且已进调律页」的装备：
        词条已满/不值得/无入口仍走各自正常分支，不会统统进调律页。
        每件产出结构化 report 追加进 output["tuning_reports"]。
        返回该件调律后（终态）指纹，供上层更新行指纹（背包位置不变、仅词条增加）。
        """
        if not equip:
            return ""
        equip_data = EquipmentData.from_dict(equip)
        affix_count = int((equip.get("_extra") or {}).get("affix_count", 0))
        logger.info(
            f"  处理装备: {self._equip_label(equip)} "
            f"quality={equip.get('quality')} affix_count={affix_count}")
        report: dict = {
            "name": name,
            "type": equip.get("type"),
            "quality": equip.get("quality"),
            "affix_count": affix_count,
        }

        # A. 词条已满 → 终局判定，不进调律
        if affix_count >= self.MAX_AFFIX:
            logger.info(f"  [{name}] 词条已满（{affix_count}），仅做终局判定")
            judgement = self._final_judge(equip_data)
            report["status"] = "already_full"
            report["final_judgement"] = judgement
            self._on_equipment_done(equip_data, judgement, report)
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        # B. 潜力判定：不值得 → 垃圾胚子
        worth, logs = judge_tuning_worthiness(
            equip_data, self._judge_configs, self._judge_schools)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        report["worthiness"] = logs
        if not worth:
            logger.info(f"  [{name}] 判定不值得调律（垃圾胚子）")
            self._on_junk_blank(equip_data, logs)
            report["status"] = "junk_blank"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        # C. 值得 → 实际调律
        food = self._decide_food(equip)
        if not self._nav_to_tune(detail_scene):
            logger.info(f"  [{name}] 未找到调律入口，跳过")
            report["status"] = "no_tune_entry"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        # 临时测试开关：只有词条未满、判定值得调律且找到入口的装备
        # 才走到这里；真实进出调律页但不执行调律，供滚动遍历压测用。
        # 装备未被改动，不走垃圾/完成后处理挂载点。
        if getattr(self, "_skip_tuning", False):
            logger.info(f"  [{name}] 值得调律，但跳过实际调律（测试开关）")
            self.click_region(self.TUNE_SCENE, "back")
            self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页
            self.click_region(detail_scene, "more_func")
            self.wait_delay("step_interval")
            report["status"] = "skip_tuning"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        rounds = 0
        while affix_count < self.MAX_AFFIX and not self.is_stopped:
            logger.info(f"  ═══ [{name}] 调律轮次 #{rounds + 1}"
                        f"（当前 {affix_count}/{self.MAX_AFFIX}）═══")
            self.wait_delay("page_refresh_wait")
            result = self._tune_once(food)
            if result is None:
                break
            rounds += 1
            affix_count += 1
            self._collect_new_affix(equip_data, result.get("tune_affix", ""))
            if affix_count >= self.MAX_AFFIX:
                break
            if not self._judge_worthiness(equip_data):
                logger.info("  新词条加入后已不再可达 顶级/优秀，提前结束调律")
                report["stop_reason"] = "判定不再可达顶级/优秀"
                break

        # 返回背包浏览页：调律页单次 back 即回到 bag_equip_detail（装备位置保持不变）。
        # 因进调律前点过「更多」弹出子菜单，back 回来后弹窗仍在，
        # 再点一次「更多」more_func 使其收起，保持背包页干净以继续遍历。
        self.click_region(self.TUNE_SCENE, "back")
        self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页（页面切换）
        self.click_region(detail_scene, "more_func")
        self.wait_delay("step_interval")

        judgement = self._final_judge(equip_data)
        report["status"] = "tuned"
        report["rounds"] = rounds
        report["final_affix_count"] = affix_count
        report["final_judgement"] = judgement
        logger.info(f"  [{name}] 调律结束：共 {rounds} 轮，词条 {affix_count}/{self.MAX_AFFIX}")
        self._on_equipment_done(equip_data, judgement, report)
        self.output.setdefault("tuning_reports", []).append(report)
        return self._make_fingerprint(equip_data.to_dict())

    # ─── 预留接口（本次留空，仅 log + pass）──────────────

    def _on_junk_blank(self, equip_data: EquipmentData, logs: list[str]):
        """调律前判定为垃圾胚子的后处理（预留：回收）。当前仅记录不动作。"""
        logger.info(f"  [垃圾胚子] {equip_data.name or equip_data.type}"
                    f" 判定不值得调律（后处理待实现）")

    def _on_equipment_done(self, equip_data: EquipmentData,
                           judgement: dict, report: dict):
        """单件调律完成/词条已满、回到背包页后的后处理挂载点。

        预留：垃圾→回收、优秀→转律、顶级→装上等。当前仅按各流派最优
        评级记录一行后 pass。
        """
        best = ""
        for r in (judgement or {}).values():
            if r.get("skipped") or r.get("not_applicable"):
                continue
            best = best or r.get("rating", "")
        logger.info(f"  [完成] {equip_data.name or equip_data.type}"
                    f" 最优评级={best or '无'}（后处理待实现）")

    # ─── 判定配置兜底 ──────────────────────────────────────

    def _ensure_judge_config(self):
        """保证 _judge_configs/_judge_schools 可用。

        优先用 run_control 注入的实时 UI 配置；未注入时回退读插件会话
        tuning.schools + tuning.keep_pvp（复刻 single_tuning._load_school_config），
        无有效配置时回退 (None, None) 即全部流派默认配置。
        """
        if getattr(self, "_judge_configs", None) is not None or \
                getattr(self, "_judge_schools", None) is not None:
            return
        self._judge_schools = None
        self._judge_configs = None
        try:
            from src.apps.yysls.session import get_plugin_session
            section = get_plugin_session().get_section("tuning")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取流派配置失败，按全部流派默认判定: {e}")
            return
        raw = section.get("schools")
        if not isinstance(raw, dict):
            return
        keep_pvp = bool(section.get("keep_pvp", False))
        enabled = {k: {**cfg, "keep_pvp": keep_pvp}
                   for k, cfg in raw.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if enabled:
            self._judge_schools = list(enabled)
            self._judge_configs = enabled

    # ─── 调律执行（移植自 single_tuning，single_tuning 保持不动）──

    @staticmethod
    def _decide_food(equip: dict) -> str:
        """按首词条数值百分比与品阶决定每轮添加的狗粮，返回材料 label（空串=不加）"""
        affix_1 = equip.get("affix_1") or {}
        cap_pct = affix_1.get("cap_pct")
        quality = equip.get("quality")
        if cap_pct is not None and cap_pct >= 90:
            logger.info(f"狗粮策略: 首词条 {cap_pct}% >= 90% → 每轮添加 金狗粮")
            return "金狗粮"
        if quality == "purple":
            logger.info(f"狗粮策略: 首词条 {cap_pct}% < 90%，紫色品阶 → 每轮添加 紫狗粮")
            return "紫狗粮"
        if quality == "gold":
            logger.info(f"狗粮策略: 首词条 {cap_pct}% < 90%，金色品阶 → 不添加狗粮")
            return ""
        logger.warning(f"狗粮策略: 品阶未知（quality={quality}），保守不添加狗粮")
        return ""

    def _judge_worthiness(self, equip_data: EquipmentData) -> bool:
        """多流派 or 潜力判定：任一流派仍可达 顶级/优秀 即值得调律"""
        worth, logs = judge_tuning_worthiness(
            equip_data, self._judge_configs, self._judge_schools)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        return worth

    def _collect_new_affix(self, equip_data: EquipmentData, text: str):
        """把调律结果的新词条补充进装备数据，供下一轮判定使用"""
        affix = get_equipment_parser().parse_affix_text(text, equip_data.level)
        if affix is None:
            logger.warning(f"调律结果词条无法解析: {text!r}，该槽位按未知处理")
            return
        equip_data.affixes.append(affix)
        equip_data.extra_data["affix_count"] = len(equip_data.affixes)
        pct = f"（{affix.cap_pct}%）" if affix.cap_pct is not None else ""
        logger.info(f"新词条: {affix.name} {affix.value}{affix.unit or ''}{pct}")

    def _final_judge(self, equip_data: EquipmentData) -> dict:
        """终局判定：含转律模拟的各流派评级上限，返回结构化结果供 report/hook 消费"""
        results = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_schools)
        for r in results.values():
            tag = ("不适用" if r["not_applicable"]
                   else "跳过" if r["skipped"] else r["rating"])
            logger.info(
                f"  终局判定 | {r['name']}: {tag}（{'；'.join(r['reasons'])}）")
        return results

    def _nav_to_tune(self, detail_scene: str) -> bool:
        """从装备详情页进入调律页，失败时停留在详情页并返回 False"""
        self.click_region(detail_scene, "more_func")
        self.wait_delay("page_refresh_wait")  # 详情页 → 「更多」弹窗展开
        tune_key = self.ocr_scene_by(
            detail_scene,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            "调律", "contains")
        if not tune_key:
            logger.info("未找到调律按钮")
            # 「更多」弹窗已开，再点一次 more_func 使其收起，保持背包页干净
            self.click_region(detail_scene, "more_func")
            self.wait_delay("step_interval")
            return False
        self.click_region(detail_scene, tune_key)
        self.wait_delay("page_refresh_wait")  # 详情页 → 调律页（页面切换）
        return True

    def _tune_once(self, food: str) -> dict | None:
        """执行一轮调律：展开材料区→按策略选狗粮→一键添加→调律→收结果。

        返回调律结果 OCR dict；返回 None 表示应终止循环（无添加入口/材料不足）。
        """
        add_scan = self.ocr_scene(self.TUNE_SCENE, ["auto_add", "auto_add_2"])
        can_add = "添加" in add_scan.get("auto_add", "")
        if "添加" in add_scan.get("auto_add_2", ""):
            self.click_region(self.TUNE_SCENE, "expand")
            self.wait_delay("page_refresh_wait")
            can_add = True
        if not can_add:
            logger.info("未找到「添加」入口，视为无法继续调律")
            return None

        if food:
            slot = self.recognize_materials_by(
                self.TUNE_SCENE, self.MATERIAL_SLOTS,
                food, "equals", group=self.MATERIAL_GROUP)
            if not slot:
                logger.warning(f"{food} 材料不足，提前结束调律")
                return None
            self.click_region(self.TUNE_SCENE, slot)
            self.wait_delay("step_interval")

        self.click_region(self.TUNE_SCENE, "auto_add")
        self.wait_delay("step_interval")
        self.click_region(self.TUNE_SCENE, "tune_btn")
        self.wait_delay("step_interval")
        self.wait_delay("after_tune_wait")

        result = self.ocr_scene(self.RESULT_SCENE, ["tune_affix", "tune_tip"])
        logger.info(f"调律结果: {result}")
        self.output.setdefault("tune_results", []).append(result)
        self.click_region(self.RESULT_SCENE, "close_btn")
        self.wait_delay("step_interval")
        return result

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
