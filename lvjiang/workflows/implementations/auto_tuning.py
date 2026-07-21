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
        """单部位 grid 遍历：初始 3 行 + 滚动循环"""
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

            # 到底检测：grid[1][1] 为空 → 滚到底了
            if not check_fp:
                logger.info("该部位遍历结束（到底）")
                return

            # 三路校验
            if not self._verify_scroll(check_fp, fp_r1, fp_r2, detail_scene):
                logger.info("该部位遍历结束（校验失败）")
                return

            # 滑动指纹窗口
            fp_r1, fp_r2 = fp_r2, fp_r3

            # 处理新行（row 3）
            has_new = False
            last_fp = ""
            for c in range(1, self.COLS + 1):
                equip = self.scan_panel(
                    self.GRID_SCENE, self.GRID_PANEL, 3, c,
                    detail_scene, self.SCAN_FIELDS)
                fp = self._make_fingerprint(equip)
                slot_key = f"r3c{c}"
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
        """三路指纹校验滚动是否正确

        Returns:
            True = 校验通过（滚动正确）
            False = 校验失败或到底
        """
        for attempt in range(max_retry):
            if check_fp == fp_r2:
                return True  # 滚动正确：旧 row2 → 新 row1

            # 微调
            if check_fp == fp_r1:
                self.drag_grid(self.GRID_SCENE, self.GRID_PANEL,
                               2, 3, "up", distance=0.2)
            else:
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

        logger.warning("滚动校验失败")
        return False

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
