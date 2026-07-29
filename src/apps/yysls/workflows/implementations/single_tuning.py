"""单件装备调律工作流 — 复刻 single_tuning.wf 并升级为全词条调律

改造点（相对 single_tuning.wf）：
1. 不再只调一次，而是循环调律直到 5 条词条全部出齐；
2. 狗粮添加不再由参数指定，按基础配置材料设置（tuning_base.materials
   的 food_rules 狗粮规则表，见「材料配置」页）逐轮自动决策：
   每轮识别材料区持有量，自上而下匹配规则（首词条/期望/品阶
   三条件），首条完全满足即生效；材料不足时按规则配置继续/跳过；
   律准石始终通过「一键添加」补足。
3. 调律前及每轮结束后执行「是否值得调律」潜力判定：遍历全部调律规则判定器
   （未实现的跳过），任一规则仍可达 顶级/优秀 即继续；每轮调律产出的
   新词条会补充进装备数据参与下一轮判定，不再达标则提前结束。
4. 调律结束后（含词条已满直接退出）执行终局判定：含转律模拟的各规则
   评级上限写入 output["final_judgement"]，供后续自动调律直接消费。
5. 临时复用调律 Tab（自动调律）的调律规则配置（不含部位）：潜力/终局
   判定仅限已启用规则及其玩法配置，避免什么装备都能命中某个规则；
   无有效配置时回退为全部规则默认配置。

参数（由 workflows.yaml 参数面板注入 variables）：
    equip_slot — 装备部位 key（main_weapon/.../wrist）
    bag_row, bag_col — 背包行列号（字符串 "1"~）
"""

from loguru import logger

from src.apps.yysls.equip_parser import EquipmentData, get_equipment_parser
from src.apps.yysls.evaluator import (
    get_rule_names, judge_equipment_potential, summarize_potential,
)
from src.apps.yysls.evaluator.tuning_rules import (
    RATING_LABELS, RATING_RANK, FoodDecision, MaterialSettings,
    get_tuning_base,
)
from src.workflows.base import BaseWorkflow


class SingleTuningWorkflow(BaseWorkflow):
    """单件装备调律工作流"""

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
    TUNE_SCENE = "equip_tune_detail"
    RESULT_SCENE = "equip_tune_result"
    MATERIAL_SLOTS = [f"material_{i}" for i in range(1, 8)]
    MATERIAL_GROUP = "调律材料"

    MAX_AFFIX = 5

    _expect_rating: str | None = None  # 当前装备期望评级 key（适用规则最高档）

    def run(self) -> dict:
        equip_slot = str(self.get_variable("equip_slot") or "main_weapon")
        bag_row = str(self.get_variable("bag_row") or "1")
        bag_col = str(self.get_variable("bag_col") or "1")
        detail_scene = (self.ARMOR_DETAIL if equip_slot in self.ARMOR_SLOTS
                        else self.WEAPON_DETAIL)
        self._judge_rule_keys, self._judge_configs = self._load_rule_config()

        # 暂时不切页面
        # self._navigate_to_equip()

        # 选中部位 + 背包格子（沿用 .wf 的 bag_{row}_{col} 区域定位）
        self.click_region(self.GRID_SCENE, equip_slot)
        self.wait_delay("step_interval")
        self.click_region(self.GRID_SCENE, f"bag_{bag_row}_{bag_col}")
        self.wait_delay("step_interval")

        # 扫描装备：词条数 + 狗粮策略依据（品阶、首词条百分比）
        fields = self.SCAN_FIELDS + (["base_attr_2"]
                                     if detail_scene == self.ARMOR_DETAIL else [])
        raw = self.ocr_scene(detail_scene, fields)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        if not equip:
            logger.error("装备扫描失败，退出")
            self._exit_from_detail(back_times=2)
            self.output["exit_code"] = -2
            return self.output

        affix_count = int((equip.get("_extra") or {}).get("affix_count", 0))
        self.output["equipment"] = equip
        logger.info(
            f"装备: {equip.get('name')} | {equip.get('type')} | "
            f"品阶={equip.get('quality')} | 词条数={affix_count}")

        if affix_count >= self.MAX_AFFIX:
            logger.info("词条满了，不用调律了")
            self._final_judge(EquipmentData.from_dict(equip))
            self._exit_from_detail(back_times=2)
            self.output["exit_code"] = -1
            return self.output

        # 进入调律前先判定是否值得调律（基于已有 1-4 条词条）
        equip_data = EquipmentData.from_dict(equip)
        if not self._judge_worthiness(equip_data):
            logger.info("判定不值得调律，跳过该装备")
            self._exit_from_detail(back_times=2)
            self.output["exit_code"] = 1
            self.output["stop_reason"] = "初始判定不值得调律"
            return self.output

        # 进入调律页
        if not self._nav_to_tune(detail_scene):
            logger.info("装备已调律完毕或未找到调律入口，无需调律")
            self._exit_from_detail(back_times=2)
            self.output["exit_code"] = -2
            return self.output

        # 调律主循环：直到词条出齐 / 判定不再达标 / 材料不足 / 用户停止
        rounds = 0
        while affix_count < self.MAX_AFFIX and not self.is_stopped:
            logger.info(f"═══ 调律轮次 #{rounds + 1}（当前词条 {affix_count}/{self.MAX_AFFIX}）═══")
            self.wait_delay("page_refresh_wait")
            result = self._tune_once(equip_data)
            if result is None:
                break
            rounds += 1
            affix_count += 1

            # 收集新词条并在下一轮开始前重新判定
            self._collect_new_affix(equip_data, result.get("tune_affix", ""))
            if affix_count >= self.MAX_AFFIX:
                break
            if not self._judge_worthiness(equip_data):
                logger.info("新词条加入后已不再可达 顶级/优秀，提前结束调律")
                self.output["stop_reason"] = "判定不再可达顶级/优秀"
                break

        logger.info(f"调律结束: 共执行 {rounds} 轮，词条数 {affix_count}/{self.MAX_AFFIX}")
        self.output["equipment"] = equip_data.to_dict()  # 刷新含新词条的装备数据
        self.output["rounds"] = rounds
        self.output["final_affix_count"] = affix_count
        self.output["exit_code"] = 0
        self._final_judge(equip_data)

        # 退出导航（沿用 .wf 的返回序列）
        self.click_region(self.TUNE_SCENE, "back")
        self.wait_delay("step_interval")
        self._exit_from_detail(back_times=3)
        return self.output

    # ─── 狗粮策略（材料配置页 tuning_base.materials.food_rules）───

    @staticmethod
    def _expect_key(results: dict) -> str | None:
        """从潜力判定结果提取装备期望评级 key（适用规则最高档）

        跳过/不适用的规则不参与；无任何适用规则时返回 None
        （狗粮规则的期望条件永不命中）。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        best: str | None = None
        for r in (results or {}).values():
            if r.get("skipped") or r.get("not_applicable"):
                continue
            key = label_to_key.get(r.get("rating", ""))
            if key and (best is None or RATING_RANK[key] > RATING_RANK[best]):
                best = key
        return best

    def _decide_food_round(self, equip_data: EquipmentData,
                           settings: MaterialSettings,
                           infos: dict | None) -> FoodDecision:
        """逐轮狗粮决策：按材料设置规则表（首词条/期望/品阶三条件）
        与本轮材料区持有量决策"""
        if not settings.food_rules:
            return FoodDecision("none", "", "未配置狗粮规则 → 不添加")
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        stocks: dict[str, int | None] = {}
        for info in (infos or {}).values():
            label = getattr(info, "type", "") or ""
            if label:
                stocks[label] = (info.owned if info.owned is not None
                                 else info.count)
        decision = settings.decide_food(
            cap_pct, self._expect_rating, equip_data.quality, stocks)
        log = logger.warning if decision.action == "skip" else logger.info
        log(f"狗粮策略: {decision.reason}")
        return decision

    # ─── 值得调律判定 & 新词条收集 ───────────────────

    @staticmethod
    def _load_rule_config() -> tuple[list[str] | None,
                                       dict[str, dict] | None]:
        """临时复用调律 Tab（自动调律）的目标规则配置（不含部位）

        读插件会话 tuning.rules（规则 key → {"enabled": bool,
        "playstyles": [名字]}），仅保留已启用规则参与判定，并合并
        全局 tuning.switches 到各规则配置；无有效配置时返回
        (None, None) 回退为全部规则默认配置。

        Returns:
            (参与判定的规则 key 列表, 规则 key → 配置 dict)
        """
        from src.apps.yysls.plugin_session import get_plugin_session

        section = get_plugin_session().get_section("tuning")
        raw = section.get("rules")
        if not isinstance(raw, dict):
            logger.info("未找到目标规则配置，按全部规则默认配置判定")
            return None, None
        switches = {str(k): bool(v)
                    for k, v in (section.get("switches") or {}).items()}
        enabled = {k: {**cfg, "switches": switches}
                   for k, cfg in raw.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if not enabled:
            logger.info("目标规则配置未启用任何规则，按全部规则默认配置判定")
            return None, None
        names = get_rule_names()
        logger.info("目标规则配置（复用自动调律）: "
                    + "、".join(names.get(k, k) for k in enabled))
        return list(enabled), enabled

    def _judge_worthiness(self, equip_data: EquipmentData) -> bool:
        """多规则 or 判定：任一规则仍可达 顶级/优秀 即值得调律

        未实现/不覆盖该部位的规则不参与判定；无规则能给出结论时
        判为不值得（结束调律）。判定明细记入 output["worthiness"]；
        同时刷新 _expect_rating（供逐轮狗粮决策的期望条件）。
        """
        results = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_rule_keys)
        worth, logs = summarize_potential(results)
        self._expect_rating = self._expect_key(results)
        for line in logs:
            logger.info(f"潜力判定 | {line}")
        self.output["worthiness"] = logs
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

    def _final_judge(self, equip_data: EquipmentData):
        """终局判定：含转律模拟的各规则评级上限，供自动调律消费

        词条已满时判定内核退化为纯转律模拟（转掉一条非首词条取最优），
        结构化结果写入 output["final_judgement"]。
        """
        results = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_rule_keys)
        for r in results.values():
            tag = ("不适用" if r["not_applicable"]
                   else "跳过" if r["skipped"] else r["rating"])
            logger.info(
                f"终局判定 | {r['name']}: {tag}（{'；'.join(r['reasons'])}）")
        self.output["final_judgement"] = results

    # ─── 导航（复刻 nav_main_to_equip.wf / nav_equip_to_tune.wf）───

    def _navigate_to_equip(self):
        result = self.ocr_scene("game_menu_page", ["wulinlu"])
        if result.get("wulinlu") != "武林录":
            self.click_region("game_main_page", "menu")
            self.wait_delay("step_interval")
        else:
            logger.info("当前在菜单页")

        self.click_region("game_menu_page", "bag")
        self.wait_delay("step_interval")

        bag_scan = self.ocr_scene(self.GRID_SCENE, ["sub_equip"])
        if "装备" not in bag_scan.get("sub_equip", ""):
            self.click_region(self.GRID_SCENE, "training")
            self.wait_delay("step_interval")

    def _nav_to_tune(self, detail_scene: str) -> bool:
        """从装备详情页进入调律页，失败时停留在详情页并返回 False"""
        self.click_region(detail_scene, "more_func")
        self.wait_delay("step_interval")

        tune_key = self.ocr_scene_by(
            detail_scene,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            "调律", "contains")
        if not tune_key:
            logger.info("未找到调律按钮")
            return False

        self.click_region(detail_scene, tune_key)
        self.wait_delay("step_interval")
        return True

    def _exit_from_detail(self, back_times: int):
        """从装备详情/背包层层返回到主界面"""
        if True:
            return
        for _ in range(back_times):
            self.click_region(self.GRID_SCENE, "back")
            self.wait_delay("step_interval")
        self.click_region("game_menu_page", "back")
        self.wait_delay("step_interval")

    # ─── 单轮调律 ──────────────────────────────────────────

    def _tune_once(self, equip_data: EquipmentData) -> dict | None:
        """执行一轮调律：展开材料区 → 逐轮狗粮决策 → 一键添加 → 调律 → 收结果。
    
        返回调律结果 OCR dict；返回 None 表示应终止循环（无添加入口 /
        材料不足 / 规则判跳过装备，原因写入 output["stop_reason"]）。
        """
        add_scan = self.ocr_scene(self.TUNE_SCENE, ["auto_add", "auto_add_2"])
    
        # 材料区未展开时先展开
        can_add = "添加" in add_scan.get("auto_add", "")
        if "添加" in add_scan.get("auto_add_2", ""):
            self.click_region(self.TUNE_SCENE, "expand")
            self.wait_delay("page_refresh_wait")
            can_add = True
    
        if not can_add:
            logger.info("未找到「添加」入口，视为无法继续调律")
            return None
    
        # 逐轮狗粮决策：每轮识别材料区持有量后按规则表决策
        settings = get_tuning_base().materials
        infos = None
        if settings.food_rules:
            infos = self.recognize_materials_info(
                self.TUNE_SCENE, self.MATERIAL_SLOTS,
                group=self.MATERIAL_GROUP)
        decision = self._decide_food_round(equip_data, settings, infos)
        if decision.action == "skip":
            self.output["stop_reason"] = decision.reason
            return None
        food = decision.food if decision.action == "feed" else ""
        if food:
            slot = next((s for s, i in (infos or {}).items()
                         if getattr(i, "type", "") == food), None)
            if not slot:
                logger.warning(f"{food} 材料槽位定位失败，提前结束调律")
                self.output["stop_reason"] = f"{food} 材料槽位定位失败"
                return None
            self.click_region(self.TUNE_SCENE, slot)
            self.wait_delay("step_interval")
    
        # 一键添加律准石 + 调律
        self.click_region(self.TUNE_SCENE, "auto_add")
        self.wait_delay("step_interval")
        self.click_region(self.TUNE_SCENE, "tune_btn")
        self.wait_delay("step_interval")
        self.wait_delay("page_refresh_wait")  # 调律结果出现（after_tune_wait 已废弃）
    
        # 收取调律结果并关闭弹窗
        result = self.ocr_scene(self.RESULT_SCENE, ["tune_affix", "tune_tip"])
        logger.info(f"调律结果: {result}")
        self.output.setdefault("tune_results", []).append(result)
        self.click_region(self.RESULT_SCENE, "close_btn")
        self.wait_delay("step_interval")
        return result
