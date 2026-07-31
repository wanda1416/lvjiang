"""自动调律工作流 — 端到端流水线

状态机三行为点（tuning_base.behavior + materials）驱动流程决策，
流派规则仅作装备预期识别（输入装备 → 输出预期评级上限）：
- 扫描处理（behavior.scan）：进调律前，传入规则预期 ≥ 进入门槛
  即进调律；未达门槛按处置表决定回收/保留（_on_scan_reject）；
- 材料处理（materials）：每轮调律开始前的律准石检查与狗粮决策；
- 结束处理（behavior.tune）：每轮调律结束后按预期评级决策
  继续调律/重置调律/回收/结束保留，词条满为边界条件（continue
  不可选，无命中默认结束保留）；背包读到已满装备同口径
  （_on_full_equipment，reset 不支持）。
回收后背包补位，由 _process_equipment 就地重读同格续处理。

实际调律执行逻辑（狗粮策略、进调律页、单轮调律、终局判定）自包含移植自
single_tuning，single_tuning 保持不动。

背包滚动遍历抽象为策略类（bag_traversal：dedup 滑动窗口去重 /
positional 位置对齐三向校验），_traverse_bag 按配置调度，默认 dedup。
"""

import random
import re
from dataclasses import replace

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData, get_equipment_parser
from lvjiang.apps.yysls.evaluator import (
    get_rule_names,
    judge_equipment_potential,
    summarize_potential,
)
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    BEHAVIOR_STAGE_LABELS,
    RATING_LABELS,
    RATING_RANK,
    STONE_LABEL,
    FoodDecision,
    MaterialSettings,
    RatingProvider,
    get_tuning_base,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    DEFAULT_TRAVERSAL,
    TRAVERSALS,
)
from lvjiang.apps.yysls.workflows.run_context import TuningContextMixin
from lvjiang.apps.yysls.workflows.tuning_doc import TuningDocWriter
from lvjiang.workflows.base import BaseWorkflow


class AutoTuningWorkflow(TuningContextMixin, BaseWorkflow):
    """自动调律工作流"""

    # 脚本元数据（供发现层暴露到日常下拉与设备端悬浮面板）。
    # 暂不定义 PARAMETERS：本流程的配置面（部位多选 + 每规则玩法多选 + 全局开关）
    # 超出现有 select/number 参数 schema 的表达力，设备端先按内置默认配置运行
    # （全部部位 + 全部规则默认判定，见 _ensure_judge_config 的回退），
    # 细粒度配置仍只在桌面调律 Tab 经 run_ctx 注入。
    DISPLAY_NAME = "自动调律"

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
    # 调律结果弹窗已并入调律页（result 视图），运行时同场景寻址
    RESULT_SCENE = "equip_tune_detail"
    MATERIAL_SLOTS = [f"material_{i}" for i in range(1, 8)]
    MATERIAL_GROUP = "调律材料"
    MAX_AFFIX = 5

    # 调律说明文档（叙事输出，与技术日志分离）：run() 内创建，
    # 创建失败/未走 run() 时保持 None，各插桩点均先判空
    _doc: TuningDocWriter | None = None
    _doc_seq = 0                # 说明文档中的装备序号
    _tune_abort_reason = ""     # _tune_once 返回 None 时的原因文案
    _materials_exhausted = False  # 大律准石低于基准，全部退出
    _stone_check_waived = False   # 询问后用户确认继续，本次运行不再检查
    _tune_ready_waived = False    # 按钮未就绪询问后确认继续，本次运行不再询问
    _expect_rating: str | None = None  # 当前装备期望评级 key（适用规则最高档）
    _round_food = ""            # 本轮实际添加的狗粮 label（空=不添加）
    _round_food_reason = ""     # 本轮狗粮决策说明（供说明文档/日志）
    _equipment_recycled = False  # 最近一次 _process_equipment 是否触发了回收
    _tune_full_recycle_mode = False  # 调满后回收模式（跳过狗粮与规则判定，调满后回收）

    @property
    def is_stopped(self) -> bool:
        """停止请求或材料耗尽（大律准石低于基准）都视为停止，
        背包遍历与部位循环全部收束，复用 F10 中断的退出路径"""
        return self._materials_exhausted or super().is_stopped

    def run(self) -> dict:
        self._materials_exhausted = False
        self._stone_check_waived = False
        self._tune_ready_waived = False
        self._ensure_judge_config()

        selected = self._resolve_selected_slots()

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

            if self.is_stopped:
                logger.info("自动调律被中断（F10/材料耗尽），保留当前页面")
                if "stop_reason" not in self.output:
                    self.output["stop_reason"] = "用户中断（F10）"
            else:
                self._navigate_back()
                logger.info("自动调律完成")
        finally:
            self._close_doc()
        return self.output

    # ─── 调律说明文档（可交付阅读的叙事输出）──────────────

    def _open_doc(self, slots: list[str]):
        """创建本次运行的说明文档并写文档头；失败只警告不中断流程。

        操作用户名由 run_control 注入 ctx.doc_username；输出目录可用
        ctx.doc_dir 覆盖（供测试），缺省 logs/tuning/。
        """
        username = self.ctx.doc_username or "default"
        try:
            self._doc = TuningDocWriter(username, self.ctx.doc_dir)
        except OSError as e:
            logger.warning(f"调律说明文档创建失败，本次不生成说明: {e}")
            self._doc = None
            return
        self._doc_seq = 0
        # 各规则配置的开关聚合（any 语义），再映射为显示名 → 状态
        merged: dict[str, bool] = {}
        for cfg in (self.ctx.judge_configs or {}).values():
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
        configs = self.ctx.judge_configs or {}
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
        """写运行小结（含 F10 中断标记）+ 成品清单并关闭文档"""
        if self._doc is None:
            return
        reports = self.output.get("tuning_reports") or []
        tuned = [r for r in reports if r.get("status") == "tuned"]
        total_rounds = sum(int(r.get("rounds") or 0) for r in tuned)
        self._doc.end_run(self.is_stopped, len(tuned), total_rounds)
        self._doc.run_summary(self._summary_items(tuned))
        self._doc.close()
        self._doc = None

    @staticmethod
    def _summary_items(tuned: list[dict]) -> list[dict]:
        """从已调律 report 筛选最终评级一般及以上的成品清单项

        取各适用规则（非跳过/非不适用）的最高评级为准；
        rating_text 罗列各适用规则的评级结论。已回收的装备不入清单。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        items: list[dict] = []
        for r in tuned:
            if r.get("recycled"):
                continue
            ratings: list[str] = []
            best: str | None = None
            for j in (r.get("final_judgement") or {}).values():
                if j.get("skipped") or j.get("not_applicable"):
                    continue
                rating = j.get("rating", "")
                ratings.append(f"{j.get('name', '')}：{rating}")
                key = label_to_key.get(rating)
                if key and (best is None
                            or RATING_RANK[key] > RATING_RANK[best]):
                    best = key
            if best is None or RATING_RANK[best] < RATING_RANK["normal"]:
                continue
            items.append({
                "name": r.get("name"),
                "type": r.get("type"),
                "level": r.get("level"),
                "quality": r.get("quality"),
                "rating_text": "；".join(ratings),
                "affixes": r.get("final_affixes") or [],
            })
        return items

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
                f"lv={equip.get('level')} quality={equip.get('quality')} fp={fp}")
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
        回收后后续装备前移补位到当前列，需留在当前列继续处理，
        而非跳到下一列（否则漏件）。
        """
        col = 2
        while col <= cols:
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, win_row, col)
            if not fp:
                logger.info(
                    f"  行{logical_row} grid[{win_row}][{col}] 空 slot → 本行到此为止")
                break
            logger.info(f"  行{logical_row} grid[{win_row}][{col}] {name} fp={fp}")
            self._process_equipment(name, equip, detail_scene,
                                    row=win_row, col=col)
            if self._equipment_recycled:
                # 回收使后续装备前移补位到当前列，留在当前列继续处理
                if not fp:
                    break   # 安全兜底：回收后同列读空 → 本行结束
                continue
            col += 1

    def _traverse_bag(self, detail_scene: str):
        """按配置选择遍历策略并执行（策略实现见 bag_traversal）

        优先级：注入配置 ctx.scroll_strategy > 插件 session tuning 节的
        scroll_strategy > 默认 DEFAULT_TRAVERSAL。回切旧方案：在
        config/session/yysls/session.json 的 tuning 节配
        "scroll_strategy": "positional"。
        """
        key = self.ctx.scroll_strategy or ""
        if not key:
            try:
                from lvjiang.apps.yysls.plugin_session import get_plugin_session
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

    def _process_equipment(self, name: str, equip: dict, detail_scene: str,
                           row: int | None = None, col: int = 1) -> str:
        """单件处理入口：回收后就地重读同格，续处理补位装备。

        row/col 为该件在背包窗口中的格位；回收使后续装备前移补位，
        有格位时重读同格续处理（保险丝防死循环）；row=None 表示调用方
        无格位信息，回收后无法回读，返回空指纹由上层按空 slot 处理。
        返回最终占位装备的（终态）指纹，供上层更新行指纹。
        设置 _equipment_recycled 标记供 _process_row_cols 判断是否
        需要在同一列继续处理（补位导致后续装备前移）。
        """
        self._equipment_recycled = False
        fp, recycled = self._process_equipment_once(name, equip, detail_scene)
        if not recycled:
            return fp
        if row is None:
            self._equipment_recycled = True
            return ""
        for _ in range(200):
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, row, col)
            if not fp:
                logger.info(f"  grid[{row}][{col}] 回收后已空，该格结束")
                self._equipment_recycled = True
                return ""
            logger.info(f"  回收补位 grid[{row}][{col}] → {name} fp={fp}")
            fp, recycled = self._process_equipment_once(name, equip,
                                                        detail_scene)
            if not recycled:
                self._equipment_recycled = True
                return fp
        self._equipment_recycled = True
        return ""

    def _process_equipment_once(self, name: str, equip: dict,
                                detail_scene: str) -> tuple[str, bool]:
        """对一件装备走完整链路：潜力判定 → 值得则调律到满 → 终局判定 → 回报。

        进入时已停在 bag_equip_detail（含 detail 区，_read_row 已点选该件）。
        未调律分支（already_full/junk_blank/no_tune_entry）保持在背包页；
        已调律分支调完后单次 back 返回背包浏览页，供遍历继续滚动。
        skip_tuning 测试开关只拦截「值得调律且已进调律页」的装备：
        词条已满/不值得/无入口仍走各自正常分支，不会统统进调律页。
        仅真正进过调律页的装备（tuned/skip_tuning）与异常的 no_tune_entry
        产出 report 追加进 output["tuning_reports"]；词条已满/垃圾胚子未做
        调律，不收集（一次遍历几百件时会淹没有效信息）。
        返回 (调律后终态指纹, 是否已回收)；已回收时指纹为空串
        （该格已被补位装备占据，由 _process_equipment 重读）。
        """
        if not equip:
            return "", False

        # 前置拦截 0：等级门槛 — 低于 min_level 直接跳过，不走任何判定
        level = equip.get("level") or 0
        min_level = get_tuning_base().min_level
        if level < min_level:
            logger.info(
                f"  [{name}] 等级 {level} < 门槛 {min_level}，直接跳过")
            return self._make_fingerprint(equip), False

        # 前置拦截 1：品阶识别异常 — 无法识别品阶说明 OCR 有问题，直接跳过
        quality = equip.get("quality")
        if not quality:
            logger.warning(
                f"  [{name}] 品阶识别失败（quality 为空），视为异常直接跳过")
            return self._make_fingerprint(equip), False

        equip_data = EquipmentData.from_dict(equip)
        affix_count = int((equip.get("_extra") or {}).get("affix_count", 0))
        logger.info(
            f"  处理装备: {self._equip_label(equip)} "
            f"quality={equip.get('quality')} affix_count={affix_count}")
        report: dict = {
            "name": name,
            "type": equip.get("type"),
            "level": equip.get("level"),
            "quality": equip.get("quality"),
            "affix_count": affix_count,
        }

        # A. 词条已满 → 终局判定 + 结束处理（full=True，仅回收/保留）；
        #    未处理过，不收集 report
        if affix_count >= self.MAX_AFFIX:
            logger.info(f"  [{name}] 词条已满（{affix_count}），仅做终局判定")
            judgement = self._final_judge(equip_data)
            report["status"] = "already_full"
            report["final_judgement"] = judgement
            if self._on_full_equipment(equip_data, judgement, report,
                                       detail_scene):
                return "", True
            return self._make_fingerprint(equip_data.to_dict()), False

        # B. 进入决策（scan 行为点）：传入规则预期评级 ≥ 进入门槛
        #    → 进调律；未达门槛按扫描处置表决定回收/保留。
        #    未处理过，不收集 report，也不写说明文档
        scan_cfg = get_tuning_base().behavior.scan
        potential = judge_equipment_potential(
            equip_data, self.ctx.judge_configs, self.ctx.judge_rule_keys)
        _, logs = summarize_potential(potential)
        expect = self._expect_key(potential)
        self._expect_rating = expect
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        report["worthiness"] = logs
        # 重置调满后回收模式标记（每件装备重新判断）
        self._tune_full_recycle_mode = False
        if (expect is None or RATING_RANK[expect]
                < RATING_RANK[scan_cfg.entry_min_rating]):
            entry_label = RATING_LABELS.get(scan_cfg.entry_min_rating,
                                            scan_cfg.entry_min_rating)
            logger.info(f"  [{name}] 预期评级未达进入门槛"
                        f"（≥{entry_label}），不进调律")
            if self._on_scan_reject(equip_data, potential, detail_scene):
                return "", True
            # 调满后回收模式：继续走调律流程
            if self._tune_full_recycle_mode:
                logger.info(f"  [{name}] 进入调满后回收模式")
            else:
                return self._make_fingerprint(equip_data.to_dict()), False

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
            return self._make_fingerprint(equip_data.to_dict()), False

        # 临时测试开关：只有词条未满、判定值得调律且找到入口的装备
        # 才走到这里；真实进出调律页但不执行调律，供滚动遍历压测用。
        # 装备未被改动，不走垃圾/完成后处理挂载点。
        if self.ctx.skip_tuning:
            logger.info(f"  [{name}] 值得调律，但跳过实际调律（测试开关）")
            if self._doc:
                self._doc.note("值得调律，但测试开关已启用，跳过实际调律")
            self.click_region(self.TUNE_SCENE, "back")
            self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页
            self.click_region(detail_scene, "more_func")
            self.wait_delay("step_interval")
            report["status"] = "skip_tuning"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict()), False

        # 结束处理支撑：首词条快照（重置后仅剩首词条）+ 本件重置计数
        tune_cfg = get_tuning_base().behavior.tune
        base_affixes = list(equip_data.affixes)
        resets_used = 0
        tune_recycle_reason = ""

        rounds = 0
        stop_reason = ""
        last_food_reason = ""
        while not self.is_stopped:
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
            # 结束处理（tune 行为点）：传入规则口径刷新预期评级
            # （供狗粮决策与说明文档）→ 行为表决策，评级按各规则
            # 自身判定语义懒取；词条满为边界条件（continue 不可达）
            full = affix_count >= self.MAX_AFFIX
            # 调满后回收模式：跳过规则判定，调满即回收
            if self._tune_full_recycle_mode:
                if full:
                    why = "调满后回收模式：词条已满，执行回收"
                    logger.info(f"  [结束处理] {why}")
                    tune_recycle_reason = why
                    stop_reason = why
                    report["stop_reason"] = stop_reason
                    if self._doc:
                        self._doc.round_decision(why)
                    break
                else:
                    why = f"调满后回收模式：继续调律（{affix_count}/{self.MAX_AFFIX}）"
                    logger.info(f"  [结束处理] {why}")
                    if self._doc:
                        self._doc.round_decision(why)
                    continue
            incoming = self._refresh_expectation(equip_data)
            part, quality, cap_pct = self._recycle_inputs(equip_data)
            rating_of = self._rating_provider(equip_data, incoming)
            action, why = tune_cfg.decide(part, quality, cap_pct, rating_of,
                                          full)
            logger.info(f"  [结束处理] {why}")
            if action == "continue":
                if self._doc:
                    self._doc.round_decision(why)
                continue
            if action == "reset":
                if self._try_reset_tune(tune_cfg, resets_used, why):
                    resets_used += 1
                    report["resets"] = resets_used
                    # 重置清空首词条以外全部词条 → 只剩首词条
                    equip_data.affixes = base_affixes[:1]
                    equip_data.extra_data["affix_count"] = 1
                    affix_count = 1
                    if self._doc:
                        self._doc.note(
                            f"重置调律（第 {resets_used} 次）：{why}")
                    continue
                # 重置次数已用尽（本地上限或按钮无次数）→ 按配置转处置
                action = tune_cfg.reset_exhausted_action
                if action == "recycle":
                    why = f"重置次数已用尽转回收（{why}）"
                    logger.info(f"  [结束处理] {why}")
            # 回收延到 back 回背包页后执行；skip = 结束保留
            if action == "recycle":
                tune_recycle_reason = why
            stop_reason = why
            report["stop_reason"] = stop_reason
            if self._doc:
                self._doc.round_decision(why)
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
        # 终态词条（含逐轮新增），供运行结束时的成品清单
        report["final_affixes"] = [a.to_dict() for a in equip_data.affixes]
        logger.info(f"  [{name}] 调律结束：共 {rounds} 轮，词条 {affix_count}/{self.MAX_AFFIX}")
        if self._doc:
            if not stop_reason:
                stop_reason = ("用户中断（F10）" if self.is_stopped
                               else "调律结束")
            self._doc.finish_equipment(rounds, affix_count, stop_reason,
                                       judgement)
        # 命中回收的装备在回到背包页后执行（阻断时不回收）
        recycled = False
        if tune_recycle_reason and not self.is_stopped:
            recycled = self._recycle_current(
                equip_data, detail_scene, "tune", tune_recycle_reason,
                report)
        self.output.setdefault("tuning_reports", []).append(report)
        if recycled:
            return "", True
        return self._make_fingerprint(equip_data.to_dict()), False

    # ─── 行为处置（按 tuning_base.behavior 行为点配置驱动）──────

    def _on_scan_reject(self, equip_data: EquipmentData, potential: dict,
                        detail_scene: str | None = None) -> bool:
        """未达进入门槛装备的扫描处置（scan 行为点）。

        处置表评级按各规则自身判定语义懒取（incoming 复用进入
        决策的判定结果，all/custom 按需另跑潜力判定并缓存；
        逐规则声明的 first_affix_only 由评级提供者注入首词条）→
        处置表首条命中；仅 recycle 动作落地，tune_full_recycle
        设置标记后返回 False（继续走调律流程），其余保留。

        Returns: True=已回收（该格已被补位装备占据）。
        """
        label = equip_data.name or equip_data.type
        cfg = get_tuning_base().behavior.scan
        if detail_scene is None or not cfg.enabled:
            logger.info(f"  [扫描处理] {label} 不进调律（处置未启用，保留）")
            return False
        part, quality, cap_pct = self._recycle_inputs(equip_data)
        action, why = cfg.decide(part, quality, cap_pct,
                                 self._rating_provider(equip_data, potential))
        if action == "tune_full_recycle":
            # 调满后回收模式：设置标记，继续走调律流程
            logger.info(f"  [扫描处理] {label} {why}（调满后回收模式）")
            self._tune_full_recycle_mode = True
            return False
        if action != "recycle":
            logger.info(f"  [扫描处理] {label} {why}")
            return False
        return self._recycle_current(equip_data, detail_scene, "scan", why)

    def _judge_by_scope(self, equip_data: EquipmentData, scope: str,
                        keys: list[str]) -> dict:
        """按判定语义跑潜力判定（行为点的预期评级识别入口）

        incoming=运行期传入配置；all=全部规则默认配置；custom=
        自选 key 集 + 默认配置（已删除的 key 运行期过滤；全部无效
        时回落全部规则，宁多保留不误收）。
        """
        if scope == "incoming":
            return judge_equipment_potential(
                equip_data, self.ctx.judge_configs, self.ctx.judge_rule_keys)
        use_keys = None
        if scope == "custom" and keys:
            known = set(get_rule_names())
            use_keys = [k for k in keys if k in known]
            unknown = [k for k in keys if k not in known]
            if unknown:
                logger.warning(f"判定声明的规则不存在: {unknown}，已忽略")
            if not use_keys:
                logger.warning("判定声明的规则全部无效，按全部规则判定")
                use_keys = None
        return judge_equipment_potential(equip_data, None, use_keys)

    def _rating_provider(self, equip_data: EquipmentData,
                         incoming: dict | None = None) -> RatingProvider:
        """构造行为表的评级提供者（同一装备当前词条状态内缓存）

        各规则按自身判定语义懒算评级（缓存键 (scope, keys,
        仅首词条)）；incoming 为已有的传入规则判定结果，作种子
        避免重复跑（基于全词条，仅首词条时不可复用）。
        first_affix_only 时只注入首词条（其余槽视作空槽由潜力
        判定自由填充），避免回收掉非首词条已成垃圾但可重置
        调律的装备。无任何适用规则（部位/品阶不在任何判定
        范围）= 无调律价值，按业务约定兜底为垃圾档。
        """
        cache: dict[tuple, str] = {}
        if incoming is not None:
            cache[("incoming", (), False)] = (
                self._expect_key(incoming) or "junk")
        label = equip_data.name or equip_data.type

        def rating_of(scope: str, keys: list[str],
                      first_affix_only: bool = False) -> str:
            # 单词条装备无需构造副本，归一到全词条缓存键
            fao = first_affix_only and len(equip_data.affixes) > 1
            ck = (scope, tuple(keys), fao)
            if ck not in cache:
                target = equip_data
                if fao:
                    target = replace(
                        equip_data, affixes=equip_data.affixes[:1],
                        extra_data={**equip_data.extra_data,
                                    "affix_count": 1})
                    logger.info(
                        f"  [行为评级] {label} 仅按首词条判定评级"
                        f"（忽略其他 {len(equip_data.affixes) - 1} 条）")
                results = self._judge_by_scope(target, scope, keys)
                cache[ck] = self._expect_key(results) or "junk"
            return cache[ck]

        return rating_of

    def _on_materials_insufficient(self, stock: int, baseline: int):
        """大律准石低于基准的后处理挂载点（预留：补货/兑换）。
        当前仅记录不动作；不足处理（跳过/结束/询问）由
        _check_stone_stock 按配置执行。"""
        logger.info(f"  [材料不足] 大律准石 {stock}/基准 {baseline}"
                    f"（补货/兑换后处理待实现，仅记录）")

    def _on_full_equipment(self, equip_data: EquipmentData,
                           judgement: dict, report: dict,
                           detail_scene: str | None = None) -> bool:
        """背包读到词条已满装备的结束处理（tune 行为点，full=True）。

        仅执行 recycle/ignore；重置需要基线词条快照（已满装备无
        快照，重置后需重读整页词条，本期不支持），reset 规则跳过
        并告警；优秀→转律、顶级→装上等后处理仍待实现。

        Returns: True=已回收。
        """
        label = equip_data.name or equip_data.type
        cfg = get_tuning_base().behavior.tune
        if detail_scene is None or not cfg.enabled:
            return False
        if self.is_stopped:
            return False  # 材料不足/用户中断属阻断，不处置
        if any(r.action == "reset" for r in cfg.rules):
            logger.warning("已满装备不支持重置调律（无基线词条快照），"
                           "reset 规则跳过")
        cfg = replace(cfg, rules=[r for r in cfg.rules
                                  if r.action != "reset"])
        part, quality, cap_pct = self._recycle_inputs(equip_data)
        action, why = cfg.decide(part, quality, cap_pct,
                                 self._rating_provider(equip_data, judgement),
                                 full=True)
        if action != "recycle":
            logger.info(f"  [结束处理] {label} {why}")
            return False
        return self._recycle_current(equip_data, detail_scene, "tune", why,
                                     report)

    @staticmethod
    def _recycle_inputs(equip_data: EquipmentData,
                        ) -> tuple[str, str | None, float | None]:
        """行为规则匹配输入：(部位, 品阶, 首词条初始数值 cap_pct)

        首词条 cap_pct 不因调律改变，各行为点可用同一输入。
        """
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        return equip_data.part, equip_data.quality, cap_pct

    def _reset_remaining(self) -> int:
        """OCR 调律页「重置调律」按钮文本，解析剩余次数

        按钮文本携带次数（如「重置调律(3)」）；兼容 x/y 样式取 x
        （剩余/总数），无斜杠取首个数字；读不到任何数字 =
        次数已用尽（返回 0）。
        """
        raw = self.ocr_scene(self.TUNE_SCENE, ["reset_tune"]).get(
            "reset_tune", "") or ""
        m = re.search(r"(\d+)\s*[/／]\s*\d+", raw)
        if m:
            return int(m.group(1))
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else 0

    def _try_reset_tune(self, cfg, resets_used: int, why: str) -> bool:
        """在调律页执行一次重置调律（点按钮 + 确认 + 二次确认）

        重置清空首词条以外的全部词条；重置后有冷却期不可连重
        （暂不做冷却判断）→ 本件硬限只重置一次。次数门槛：
        本地计数达配置上限即止；按钮文本剩余次数另作硬门
        （读不到数字 = 用尽，不重置）。成功后调律页保持打开。
        """
        if resets_used >= 1:
            # 重置后进入冷却期，本次工作内不可再重置该件
            logger.info("  本件已重置过一次，冷却期内不再重置")
            return False
        if resets_used >= cfg.max_resets:
            logger.info(f"  重置次数已达上限（{cfg.max_resets}），不再重置")
            return False
        remaining = self._reset_remaining()
        if remaining <= 0:
            logger.info("  重置调律按钮无剩余次数，不再重置")
            return False
        logger.info(f"  执行重置调律（剩余次数 OCR: {remaining}）：{why}")
        self.click_region(self.TUNE_SCENE, "reset_tune")
        self.wait_delay("page_refresh_wait")  # 重置确认弹窗
        self.click_region(self.TUNE_SCENE, "reset_confirm")
        # 游戏在两次确认间强制等 5s，二次确认按钮才可点 → 等 6-7s
        self.wait_seconds(random.uniform(6.0, 7.0))
        self.click_region(self.TUNE_SCENE, "reset_confirm_2")
        self.wait_delay("page_refresh_wait")  # 词条重置，调律页刷新
        return True

    def _recycle_current(self, equip_data: EquipmentData, detail_scene: str,
                         stage: str, reason: str,
                         report: dict | None = None) -> bool:
        """回收当前详情页选中的装备：更多 → 子菜单「回收」→ 确认弹窗

        进入时背包详情页无弹窗；未找到回收按钮时收起弹窗返回
        False（装备保留原地）。成功后背包刷新、后续装备前移补位。
        """
        label = equip_data.name or equip_data.type
        stage_label = BEHAVIOR_STAGE_LABELS.get(stage, stage)
        logger.info(f"  [{stage_label}] 回收 {label}：{reason}")
        self.click_region(detail_scene, "more_func")
        self.wait_delay("page_refresh_wait")  # 详情页 → 「更多」弹窗展开
        key = self.ocr_scene_by(
            detail_scene,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            "回收", "contains")
        if not key:
            logger.warning("未找到回收按钮，装备保留")
            self.click_region(detail_scene, "more_func")
            self.wait_delay("step_interval")
            return False
        self.click_region(detail_scene, key)
        self.wait_delay("page_refresh_wait")  # 回收确认弹窗
        self.click_region(detail_scene, "recycle_confirm")
        self.wait_delay("page_refresh_wait")  # 回收完成，背包刷新补位
        if report is not None:
            report["recycled"] = True
            report["recycle_reason"] = reason
        self.output.setdefault("recycled_items", []).append({
            "name": equip_data.name, "type": equip_data.type,
            "quality": equip_data.quality,
            "stage": stage, "reason": reason,
        })
        logger.info(f"  [{stage_label}] {label} 已回收")
        return True

    # ─── 判定配置兜底 ──────────────────────────────────────

    def _ensure_judge_config(self):
        """保证 ctx.judge_configs/judge_rule_keys 可用。

        优先用 run_control 注入的实时 UI 配置；未注入时回退读插件会话
        tuning.rules + tuning.switches（复刻 single_tuning._load_rule_config），
        无有效配置时回退 (None, None) 即全部规则默认配置。
        """
        ctx = self.ctx
        if ctx.judge_configs is not None or ctx.judge_rule_keys is not None:
            return
        try:
            from lvjiang.apps.yysls.plugin_session import get_plugin_session
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
            ctx.judge_rule_keys = list(enabled)
            ctx.judge_configs = enabled

    def _resolve_selected_slots(self) -> list[str]:
        """调律部位：优先 UI 注入的 ctx.selected_slots；设备端经 task_runner
        启动时 ctx 未注入（selected_slots=None），回退读插件会话
        tuning.selected_slots（与 _ensure_judge_config 读 rules/switches 对称）；
        仍无有效配置时按全部部位。"""
        selected = self.ctx.selected_slots
        if selected is None:
            try:
                from lvjiang.apps.yysls.plugin_session import get_plugin_session
                raw = (get_plugin_session().get_section("tuning")
                       .get("selected_slots"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"读取调律部位配置失败，按全部部位: {e}")
                raw = None
            if isinstance(raw, list):
                valid = set(self.WEAPON_SLOTS) | set(self.ARMOR_SLOTS)
                selected = [s for s in raw if s in valid]
        if not selected:
            selected = self.WEAPON_SLOTS + self.ARMOR_SLOTS
        return selected

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
        低于基准判材料不足，按配置的不足处理执行：skip=跳过该装备
        （继续遍历）；ask=confirm 弹窗询问，确认继续则本次运行不再
        检查，拒绝同 abort；abort=置 _materials_exhausted 使 is_stopped
        恒真，全部退出。材料区找不到大律准石视为已耗尽；数量 OCR
        失败时警告放行（识别波动不误杀整次运行）。

        Returns:
            True=可继续调律；False=材料不足，本件终止（是否全退
            由 _materials_exhausted 决定）
        """
        if not settings.stone_check_enabled or self._stone_check_waived:
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
        action = settings.stone_insufficient_action
        if action == "ask" and self._confirm_continue(
                f"{reason}，是否继续调律？"):
            logger.warning(f"{reason}，用户确认继续，本次运行不再检查")
            self._stone_check_waived = True
            return True
        if action == "skip":
            logger.warning(f"{reason}，跳过该装备")
            self._tune_abort_reason = f"{reason}，跳过该装备"
            self._on_materials_insufficient(stock, settings.stone_min_count)
            return False
        # abort / ask 拒绝 → 全部退出
        logger.warning(f"{reason}，终止全部调律")
        self._tune_abort_reason = reason
        self._materials_exhausted = True
        self.output["stop_reason"] = reason
        self._on_materials_insufficient(stock, settings.stone_min_count)
        return False

    def _confirm_continue(self, message: str) -> bool:
        """走 DSL confirm 内置函数弹窗询问用户

        有 engine 引用时经 engine._ui_callback 调度到 Qt 主线程，
        无则回退平台原生弹窗（confirm 内置函数自行处理）。
        """
        return bool(self.call_function("confirm", [message],
                                       engine=self._engine))

    def _ensure_tune_ready(self, settings: MaterialSettings) -> bool:
        """一键添加后确认「调律」按钮已就绪（文字含「调律」）

        添加失败时按钮文字不变——多半是材料不足（一键添加无可用
        材料），盲点「调律」只会空转。未就绪先等一拍重扫一次（防 UI
        刷新慢 / OCR 波动误杀），仍未就绪走材料不足处理：本件必然
        结束（按钮点不动），是否全退按石头检查的不足处理决定；
        未启用检查时也要兜底，按 skip 语义只跳过本件。ask 确认继续
        后本次运行不再重复询问（后续未就绪直接按跳过处理）。

        Returns:
            True=按钮就绪可点击；False=本件终止（是否全退由
            _materials_exhausted 决定），原因已存 _tune_abort_reason
        """
        btn = ""
        for attempt in range(2):
            if attempt:
                self.wait_delay("page_refresh_wait")
            btn = self.ocr_scene(self.TUNE_SCENE, ["tune_btn"]).get(
                "tune_btn", "") or ""
            if "调律" in btn:
                return True
        reason = (f"一键添加后「调律」按钮未就绪"
                  f"（OCR: {btn or '空'}），疑似材料不足")
        action = (settings.stone_insufficient_action
                  if settings.stone_check_enabled else "skip")
        if action == "ask":
            if self._tune_ready_waived:
                action = "skip"
            elif self._confirm_continue(
                    f"{reason}，是否跳过本件继续处理后续装备？"):
                self._tune_ready_waived = True
                action = "skip"
            else:
                action = "abort"
        if action == "skip":
            logger.warning(f"{reason}，结束本件调律")
            self._tune_abort_reason = f"{reason}，结束本件调律"
            return False
        # abort / ask 拒绝 → 全部退出
        logger.warning(f"{reason}，终止全部调律")
        self._tune_abort_reason = reason
        self._materials_exhausted = True
        self.output["stop_reason"] = reason
        return False

    def _refresh_expectation(self, equip_data: EquipmentData) -> dict:
        """按传入规则刷新装备预期评级（每轮调律结束后取值）

        同步写入 _expect_rating（供狗粮决策与说明文档）；行为表
        评级另经评级提供者按各规则判定语义懒取。

        Returns: 传入规则判定结果（供评级提供者作 incoming 种子）。
        """
        results = self._judge_by_scope(equip_data, "incoming", [])
        _, logs = summarize_potential(results)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        self._expect_rating = self._expect_key(results)
        return results

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
            equip_data, self.ctx.judge_configs, self.ctx.judge_rule_keys)
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

        # 调满后回收模式：跳过狗粮决策
        food = ""
        if self._tune_full_recycle_mode:
            self._round_food = ""
            self._round_food_reason = "调满后回收模式，跳过狗粮添加"
            logger.info(f"狗粮策略: {self._round_food_reason}")
        else:
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
        # 添加后确认按钮真的变成了「调律」，未就绪走材料不足处理
        if not self._ensure_tune_ready(settings):
            return None
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
