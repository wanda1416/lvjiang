"""自动调律工作流 — 端到端流水线

遍历背包各部位 → 对每件用潜力判定（judge_tuning_worthiness）决定是否
值得调律 → 值得的实际调律到词条满，产出结构化 report 回报给遍历
循环（output["tuning_reports"]，词条已满/垃圾胚子不收集；每轮调律
结果挂在本件 report 的 tune_results 下）；并为「垃圾胚子处理」与「调律后回背包页
的回收/转律/装上」预留空接口（_on_junk_blank / _on_equipment_done）。

实际调律执行逻辑（狗粮策略、进调律页、单轮调律、终局判定）自包含移植自
single_tuning，single_tuning 保持不动。

背包滚动遍历抽象为策略类（bag_traversal：dedup 滑动窗口去重 /
positional 位置对齐三向校验），_traverse_bag 按配置调度，默认 dedup。
"""

from loguru import logger

from src.apps.yysls.equip_parser import EquipmentData, get_equipment_parser
from src.apps.yysls.evaluator import (
    Rating, get_rule_names, judge_equipment_potential, summarize_potential,
)
from src.apps.yysls.evaluator.tuning_rules import (
    RATING_LABELS, RATING_RANK, STONE_LABEL, FoodDecision, MaterialSettings,
    get_tuning_base,
)
from src.apps.yysls.workflows.implementations.bag_traversal import (
    DEFAULT_TRAVERSAL, TRAVERSALS,
)
from src.apps.yysls.workflows.tuning_doc import TuningDocWriter
from src.workflows.base import BaseWorkflow


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

    # 调律说明文档（叙事输出，与技术日志分离）：run() 内创建，
    # 创建失败/未走 run() 时保持 None，各插桩点均先判空
    _doc: TuningDocWriter | None = None
    _doc_seq = 0                # 说明文档中的装备序号
    _tune_abort_reason = ""     # _tune_once 返回 None 时的原因文案
    _materials_exhausted = False  # 大律准石低于基准，全部退出
    _expect_rating: str | None = None  # 当前装备期望评级 key（适用规则最高档）
    _round_food = ""            # 本轮实际添加的狗粮 label（空=不添加）
    _round_food_reason = ""     # 本轮狗粮决策说明（供说明文档/日志）

    @property
    def is_stopped(self) -> bool:
        """停止请求或材料耗尽（大律准石低于基准）都视为停止，
        背包遍历与部位循环全部收束，复用 F10 中断的退出路径"""
        return self._materials_exhausted or super().is_stopped

    def run(self) -> dict:
        self._materials_exhausted = False
        self._ensure_judge_config()

        selected = getattr(self, '_selected_slots', None)
        if not selected:
            selected = self.WEAPON_SLOTS + self.ARMOR_SLOTS

        self._open_doc(selected)
        try:
            self._navigate_to_equip()

            for slot in selected:
                if self.is_stopped:
                    break
                if slot in self.WEAPON_SLOTS:
                    self._process_slot_group(slot, self.WEAPON_DETAIL)
                elif slot in self.ARMOR_SLOTS:
                    self._process_slot_group(slot, self.ARMOR_DETAIL)

            self._navigate_back()
            logger.info("自动调律完成")
        finally:
            self._close_doc()
        return self.output

    # ─── 调律说明文档（可交付阅读的叙事输出）──────────────

    def _open_doc(self, slots: list[str]):
        """创建本次运行的说明文档并写文档头；失败只警告不中断流程。

        操作用户名由 run_control 注入 _doc_username；输出目录可用
        _doc_dir 覆盖（供测试），缺省 logs/tuning/。
        """
        username = getattr(self, "_doc_username", "") or "default"
        try:
            self._doc = TuningDocWriter(
                username, getattr(self, "_doc_dir", None))
        except OSError as e:
            logger.warning(f"调律说明文档创建失败，本次不生成说明: {e}")
            self._doc = None
            return
        self._doc_seq = 0
        # 各规则配置的开关聚合（any 语义），再映射为显示名 → 状态
        merged: dict[str, bool] = {}
        for cfg in (self._judge_configs or {}).values():
            for k, v in (cfg.get("switches") or {}).items():
                merged[str(k)] = merged.get(str(k), False) or bool(v)
        try:
            names = get_tuning_base().switches
        except Exception:
            names = {}
        switches = {names.get(k, k): v for k, v in merged.items()}
        self._doc.start_run(username, self._describe_rules(), slots, switches)
        logger.info(f"调律说明文档: {self._doc.path}")

    def _describe_rules(self) -> list[str]:
        """由判定配置生成规则描述：显示名（玩法：勾选项）"""
        configs = self._judge_configs or {}
        if not configs:
            return []
        names = get_rule_names()
        descs = []
        for key, cfg in configs.items():
            name = names.get(key, key)
            playstyles = cfg.get("playstyles")
            if playstyles:
                name += f"（玩法：{'、'.join(playstyles)}）"
            descs.append(name)
        return descs

    def _close_doc(self):
        """写运行小结（含 F10 中断标记）并关闭文档"""
        if self._doc is None:
            return
        reports = self.output.get("tuning_reports") or []
        tuned = [r for r in reports if r.get("status") == "tuned"]
        total_rounds = sum(int(r.get("rounds") or 0) for r in tuned)
        self._doc.end_run(self.is_stopped, len(tuned), total_rounds)
        self._doc.close()
        self._doc = None

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

    # ─── 背包遍历（滚动策略实现见 bag_traversal）──────────────

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

    def _traverse_bag(self, detail_scene: str):
        """按配置选择遍历策略并执行（策略实现见 bag_traversal）

        优先级：注入属性 _scroll_strategy > 插件 session tuning 节的
        scroll_strategy > 默认 DEFAULT_TRAVERSAL。回切旧方案：在
        config/local/yysls/session.json 的 tuning 节配
        "scroll_strategy": "positional"。
        """
        key = getattr(self, "_scroll_strategy", "") or ""
        if not key:
            try:
                from src.apps.yysls.plugin_session import get_plugin_session
                key = (get_plugin_session().get_section("tuning")
                       .get("scroll_strategy") or "")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"读取遍历策略配置失败，用默认: {e}")
        if key and key not in TRAVERSALS:
            logger.warning(f"未知遍历策略 {key!r}，回落默认 {DEFAULT_TRAVERSAL}")
        if key not in TRAVERSALS:
            key = DEFAULT_TRAVERSAL
        logger.info(f"背包遍历策略: {key}")
        TRAVERSALS[key]().traverse(self, detail_scene)


    # ─── 单件装备处理（潜力判定 + 实际调律 + 回报）────────

    def _process_equipment(self, name: str, equip: dict, detail_scene: str) -> str:
        """对一件装备走完整链路：潜力判定 → 值得则调律到满 → 终局判定 → 回报。

        进入时已停在 bag_equip_detail（含 detail 区，_read_row 已点选该件）。
        未调律分支（already_full/junk_blank/no_tune_entry）保持在背包页；
        已调律分支调完后单次 back 返回背包浏览页，供遍历继续滚动。
        skip_tuning 测试开关只拦截「值得调律且已进调律页」的装备：
        词条已满/不值得/无入口仍走各自正常分支，不会统统进调律页。
        仅真正进过调律页的装备（tuned/skip_tuning）与异常的 no_tune_entry
        产出 report 追加进 output["tuning_reports"]；词条已满/垃圾胚子未做
        任何处理，不收集（一次遍历几百件时会淹没有效信息）。
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

        # A. 词条已满 → 终局判定，不进调律；未处理过，不收集 report
        if affix_count >= self.MAX_AFFIX:
            logger.info(f"  [{name}] 词条已满（{affix_count}），仅做终局判定")
            judgement = self._final_judge(equip_data)
            report["status"] = "already_full"
            report["final_judgement"] = judgement
            self._on_equipment_done(equip_data, judgement, report)
            return self._make_fingerprint(equip_data.to_dict())

        # B. 潜力判定：不值得 → 垃圾胚子；未处理过，不收集 report，
        #    也不写说明文档（只写实际进入调律的装备）
        potential = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_rule_keys)
        worth, logs = summarize_potential(potential)
        self._expect_rating = self._expect_key(potential)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        report["worthiness"] = logs
        if not worth:
            logger.info(f"  [{name}] 判定不值得调律（垃圾胚子）")
            self._on_junk_blank(equip_data, logs)
            return self._make_fingerprint(equip_data.to_dict())

        # C. 值得 → 实际调律；说明文档开装备节（只写命中的规则）
        if self._doc:
            self._doc_seq += 1
            self._doc.start_equipment(self._doc_seq, equip)
            self._doc.worthiness_matched(potential)
        if not self._nav_to_tune(detail_scene):
            logger.info(f"  [{name}] 未找到调律入口，跳过")
            report["status"] = "no_tune_entry"
            if self._doc:
                self._doc.note("已符合规则但未找到调律入口，跳过本件")
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        # 临时测试开关：只有词条未满、判定值得调律且找到入口的装备
        # 才走到这里；真实进出调律页但不执行调律，供滚动遍历压测用。
        # 装备未被改动，不走垃圾/完成后处理挂载点。
        if getattr(self, "_skip_tuning", False):
            logger.info(f"  [{name}] 值得调律，但跳过实际调律（测试开关）")
            if self._doc:
                self._doc.note("值得调律，但测试开关已启用，跳过实际调律")
            self.click_region(self.TUNE_SCENE, "back")
            self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页
            self.click_region(detail_scene, "more_func")
            self.wait_delay("step_interval")
            report["status"] = "skip_tuning"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict())

        rounds = 0
        stop_reason = ""
        last_food_reason = ""
        while affix_count < self.MAX_AFFIX and not self.is_stopped:
            logger.info(f"  ═══ [{name}] 调律轮次 #{rounds + 1}"
                        f"（当前 {affix_count}/{self.MAX_AFFIX}）═══")
            self.wait_delay("page_refresh_wait")
            result = self._tune_once(equip_data)
            if result is None:
                stop_reason = self._tune_abort_reason or "无法继续调律"
                report["stop_reason"] = stop_reason
                if self._doc:
                    self._doc.note(f"{stop_reason}，结束调律")
                break
            rounds += 1
            affix_count += 1
            # 每轮结果挂在本件 report 下，与装备一一对应
            report.setdefault("tune_results", []).append(result)
            new_affix = self._collect_new_affix(
                equip_data, result.get("tune_affix", "")) \
                or result.get("tune_affix", "")
            if self._doc:
                if self._round_food_reason != last_food_reason:
                    self._doc.food_strategy(self._round_food_reason)
                    last_food_reason = self._round_food_reason
                self._doc.tune_round(rounds, self._round_food, new_affix)
            if affix_count >= self.MAX_AFFIX:
                stop_reason = "词条已满，调律完成"
                if self._doc:
                    self._doc.round_decision(
                        f"词条已满（{self.MAX_AFFIX}/{self.MAX_AFFIX}），调律完成")
                break
            worth_more, matched = self._judge_worthiness(equip_data)
            if not worth_more:
                logger.info("  新词条加入后已不再可达 顶级/优秀，提前结束调律")
                stop_reason = "判定不再可达顶级/优秀"
                report["stop_reason"] = stop_reason
                if self._doc:
                    self._doc.round_decision(
                        "新词条加入后不再可达 顶级/优秀，结束调律")
                break
            if self._doc:
                self._doc.round_decision(
                    f"仍可达 顶级/优秀（{'、'.join(matched)}），继续")

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
        if self._doc:
            if not stop_reason:
                stop_reason = ("用户中断（F10）" if self.is_stopped
                               else "调律结束")
            self._doc.finish_equipment(rounds, affix_count, stop_reason,
                                       judgement)
        self._on_equipment_done(equip_data, judgement, report)
        self.output.setdefault("tuning_reports", []).append(report)
        return self._make_fingerprint(equip_data.to_dict())

    # ─── 预留接口（本次留空，仅 log + pass）──────────────

    def _on_junk_blank(self, equip_data: EquipmentData, logs: list[str]):
        """调律前判定为垃圾胚子的后处理（预留：回收）。当前仅记录不动作。"""
        logger.info(f"  [垃圾胚子] {equip_data.name or equip_data.type}"
                    f" 判定不值得调律（后处理待实现）")

    def _on_materials_insufficient(self, stock: int, baseline: int):
        """大律准石低于基准的后处理挂载点（预留：补货/兑换）。
        当前仅记录不动作，全部退出由 _materials_exhausted 驱动。"""
        logger.info(f"  [材料不足] 大律准石 {stock}/基准 {baseline}"
                    f"（不足处理待实现）")

    def _on_equipment_done(self, equip_data: EquipmentData,
                           judgement: dict, report: dict):
        """单件调律完成/词条已满、回到背包页后的后处理挂载点。

        预留：垃圾→回收、优秀→转律、顶级→装上等。当前仅按各规则最优
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
        """保证 _judge_configs/_judge_rule_keys 可用。

        优先用 run_control 注入的实时 UI 配置；未注入时回退读插件会话
        tuning.rules + tuning.switches（复刻 single_tuning._load_rule_config），
        无有效配置时回退 (None, None) 即全部规则默认配置。
        """
        if getattr(self, "_judge_configs", None) is not None or \
                getattr(self, "_judge_rule_keys", None) is not None:
            return
        self._judge_rule_keys = None
        self._judge_configs = None
        try:
            from src.apps.yysls.plugin_session import get_plugin_session
            section = get_plugin_session().get_section("tuning")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取调律规则配置失败，按全部规则默认判定: {e}")
            return
        raw = section.get("rules")
        if not isinstance(raw, dict):
            return
        switches = {str(k): bool(v)
                    for k, v in (section.get("switches") or {}).items()}
        enabled = {k: {**cfg, "switches": switches}
                   for k, cfg in raw.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if enabled:
            self._judge_rule_keys = list(enabled)
            self._judge_configs = enabled

    # ─── 调律执行（移植自 single_tuning，single_tuning 保持不动）──

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
        与本轮材料区持有量决策（与石头检查共用同一次识别）"""
        if not settings.food_rules:
            return FoodDecision("none", "", "未配置狗粮规则 → 不添加")
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        stocks: dict[str, int | None] = {}
        for info in (infos or {}).values():
            label = getattr(info, "type", "") or ""
            if not label:
                continue
            # 低置信度误匹配的同名幽灵槽（数量 None）不得覆盖真槽
            if label in stocks and stocks[label] is not None:
                continue
            stocks[label] = (info.owned if info.owned is not None
                             else info.count)
        decision = settings.decide_food(
            cap_pct, self._expect_rating, equip_data.quality, stocks)
        log = logger.warning if decision.action == "skip" else logger.info
        log(f"狗粮策略: {decision.reason}")
        return decision

    def _check_stone_stock(self, settings: MaterialSettings,
                           infos: dict | None) -> bool:
        """大律准石数量检查（材料设置可开关，默认关闭）

        基于调律页材料区识别结果（infos，与逐轮狗粮决策共用同一次
        识别），取大律准石持有量（x/y 优先取 y，无斜杠取显示数字）；
        低于基准判材料不足，置 _materials_exhausted 使 is_stopped 恒真，
        全部退出。材料区找不到大律准石视为已耗尽；数量 OCR 失败时
        警告放行（识别波动不误杀整次运行）。

        Returns:
            True=可继续调律；False=材料不足，应终止全部流程
        """
        if not settings.stone_check_enabled:
            return True
        stone = next((i for i in (infos or {}).values()
                      if getattr(i, "type", "") == STONE_LABEL), None)
        if stone is None:
            stock = 0  # 材料区无大律准石，视为已耗尽
        else:
            stock = stone.owned if stone.owned is not None else stone.count
            if stock is None:
                logger.warning("大律准石数量识别失败，本轮跳过数量检查")
                return True
        if stock >= settings.stone_min_count:
            logger.debug(
                f"大律准石库存 {stock} >= 基准 {settings.stone_min_count}")
            return True
        reason = (f"大律准石 {stock} < 基准 "
                  f"{settings.stone_min_count}，材料不足")
        logger.warning(f"{reason}，终止全部调律")
        self._tune_abort_reason = reason
        self._materials_exhausted = True
        self.output["stop_reason"] = reason
        self._on_materials_insufficient(stock, settings.stone_min_count)
        return False

    def _judge_worthiness(self, equip_data: EquipmentData) -> tuple[bool, list[str]]:
        """多规则 or 潜力判定：任一规则仍可达 顶级/优秀 即值得调律

        Returns:
            (是否值得, 命中 顶级/优秀 的规则显示名列表，供说明文档)
        """
        results = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_rule_keys)
        worth, logs = summarize_potential(results)
        self._expect_rating = self._expect_key(results)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        matched = [r["name"] for r in results.values()
                   if not r["skipped"] and not r["not_applicable"]
                   and r["rating"] in (Rating.TOP.value,
                                       Rating.EXCELLENT.value)]
        return worth, matched

    def _collect_new_affix(self, equip_data: EquipmentData, text: str) -> str:
        """把调律结果的新词条补充进装备数据，供下一轮判定使用

        Returns:
            新词条展示文本（供说明文档）；无法解析时原样返回 OCR
            文本并标注未能解析。
        """
        affix = get_equipment_parser().parse_affix_text(text, equip_data.level)
        if affix is None:
            logger.warning(f"调律结果词条无法解析: {text!r}，该槽位按未知处理")
            return f"{text}（未能解析）"
        equip_data.affixes.append(affix)
        equip_data.extra_data["affix_count"] = len(equip_data.affixes)
        pct = f"（{affix.cap_pct}%）" if affix.cap_pct is not None else ""
        desc = f"{affix.name} {affix.value}{affix.unit or ''}{pct}"
        logger.info(f"新词条: {desc}")
        return desc

    def _final_judge(self, equip_data: EquipmentData) -> dict:
        """终局判定：含转律模拟的各规则评级上限，返回结构化结果供 report/hook 消费"""
        results = judge_equipment_potential(
            equip_data, self._judge_configs, self._judge_rule_keys)
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

    def _tune_once(self, equip_data: EquipmentData) -> dict | None:
        """执行一轮调律：展开材料区→逐轮狗粮决策→一键添加→调律→收结果。

        石头检查与狗粮决策共用同一次材料区识别；决策结果存入
        _round_food/_round_food_reason 供调用方写说明文档。
        返回调律结果 OCR dict（由调用方挂进本件 report 的 tune_results）；
        返回 None 表示应终止循环（无添加入口/材料不足/规则判跳过装备，
        原因文案存入 _tune_abort_reason 供说明文档）。
        添加过狗粮时，关闭结果弹窗后还会补扫一次狗粮返还弹窗并补关。
        """
        self._tune_abort_reason = ""
        self._round_food = ""
        self._round_food_reason = ""
        add_scan = self.ocr_scene(self.TUNE_SCENE, ["auto_add", "auto_add_2"])
        can_add = "添加" in add_scan.get("auto_add", "")
        if "添加" in add_scan.get("auto_add_2", ""):
            self.click_region(self.TUNE_SCENE, "expand")
            self.wait_delay("page_refresh_wait")
            can_add = True
        if not can_add:
            logger.info("未找到「添加」入口，视为无法继续调律")
            self._tune_abort_reason = "未找到「添加」入口"
            return None

        # 一次材料区识别：大律准石检查 + 逐轮狗粮决策共用
        settings = get_tuning_base().materials
        infos = None
        if settings.stone_check_enabled or settings.food_rules:
            infos = self.recognize_materials_info(
                self.TUNE_SCENE, self.MATERIAL_SLOTS,
                group=self.MATERIAL_GROUP)
        if not self._check_stone_stock(settings, infos):
            return None

        decision = self._decide_food_round(equip_data, settings, infos)
        self._round_food_reason = decision.reason
        if decision.action == "skip":
            self._tune_abort_reason = decision.reason
            return None
        food = decision.food if decision.action == "feed" else ""
        self._round_food = food
        if food:
            # 同名幽灵槽防护：只认数量有效的槽位
            slot = next(
                (s for s, i in (infos or {}).items()
                 if getattr(i, "type", "") == food
                 and (getattr(i, "owned", None) is not None
                      or getattr(i, "count", None) is not None)), None)
            if not slot:
                logger.warning(f"{food} 材料槽位定位失败，提前结束调律")
                self._tune_abort_reason = f"{food} 材料槽位定位失败"
                return None
            self.click_region(self.TUNE_SCENE, slot)
            self.wait_delay("step_interval")

        self.click_region(self.TUNE_SCENE, "auto_add")
        self.wait_delay("step_interval")
        self.click_region(self.TUNE_SCENE, "tune_btn")
        self.wait_delay("step_interval")
        self.wait_delay("page_refresh_wait")  # 调律结果出现（after_tune_wait 已废弃）

        result = self.ocr_scene(self.RESULT_SCENE, ["tune_affix", "tune_tip"])
        logger.info(f"调律结果: {result}")
        self.click_region(self.RESULT_SCENE, "close_btn")
        self.wait_delay("step_interval")

        # 狗粮返还机制：添加过狗粮的轮次，关闭结果弹窗后可能命中概率返还，
        # 再弹一个无边框弹窗（同样 tune_tip「点击空白区域关闭」）。若不补关，
        # 它会挡住「一键添加」等后续点击，导致误判无法继续调律。
        if food:
            self.wait_delay("page_refresh_wait")
            bonus = self.ocr_scene(self.RESULT_SCENE, ["tune_tip"])
            if bonus.get("tune_tip"):
                logger.info(f"检测到狗粮返还弹窗: {bonus}，补点一次关闭")
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
