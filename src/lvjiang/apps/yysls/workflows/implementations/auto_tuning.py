"""自动调律工作流 — 端到端流水线（编排层）

状态机三行为点（基础规则组 behavior + materials）驱动流程决策，
流派规则仅作装备预期识别（输入装备 → 输出预期评级上限）：
- 扫描处理（behavior.scan）：进调律前，传入规则预期 ≥ 进入门槛
  即进调律；未达门槛按处置表决定回收/保留（_on_scan_reject）；
- 材料处理（materials）：每轮调律开始前的律准石检查与狗粮决策；
- 结束处理（behavior.tune）：每轮调律结束后按预期评级决策
  继续调律/重置调律/回收/结束保留，词条满为边界条件（continue
  不可选，无命中默认结束保留）；背包读到已满装备走扫描处理
  同一路径（tune_full_recycle 视为直接回收）。
回收后背包补位，由 _process_equipment 就地重读同格续处理。

调律功能按职责拆分为 tuning 包下的应用组件（组合引用）：
- TuningJudge: 判定与评级（纯逻辑）
- TuningExecutor: 调律执行（材料检查、狗粮决策、单轮调律）
- TuningNavigator: 导航（页面跳转、词条收集）
- TuningResetter: 调律重置
- TuningRecycler: 装备回收

背包滚动遍历抽象为策略类（bag_traversal：dedup 滑动窗口去重 /
positional 位置对齐三向校验），_traverse_bag 按配置调度，默认 dedup。

⚠️ 警告：禁止擅自软降级或吞异常
本工作流的所有配置缺失/无效场景必须抛异常中断，不得静默回落
到“合理默认值”。配置错误应当立即暴露给用户修正，而不是用
猜测的默认值继续运行导致调律结果不可预期。涉及方法：
_traverse_bag、_ensure_base_group、_ensure_judge_config、
_resolve_selected_slots。
"""

import hashlib
import json

from loguru import logger

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.tune_slots import SLOT_LABELS
from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.evaluator import (
    judge_equipment_potential,
    summarize_potential,
)
from lvjiang.apps.yysls.core.tuning_rules import (
    RATING_LABELS,
    RATING_RANK,
    get_tune_config,
)
from lvjiang.apps.yysls.telemetry import probe as telemetry_probe
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    DEFAULT_TRAVERSAL,
    TRAVERSALS,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import (
    BehaviorAction,
    EquipmentProcessingResult,
    EquipmentSession,
    RecycleOutcome,
    SlotEffect,
    TuningExecutor,
    TuningJudge,
    TuningMode,
    TuningNavigator,
    TuningRecorder,
    TuningRecycler,
    TuningResetter,
    TuningRouteStrategy,
    TuningRunState,
    create_tuning_route_strategy,
)
from lvjiang.apps.yysls.workflows.tuning_context import TuningContextMixin
from lvjiang.apps.yysls.workflows.tuning_doc import TuningDocWriter
from lvjiang.workflows.base import BaseWorkflow

from .....i18n import tr


def _best_rating(judgement: dict | None) -> str | None:
    """终局判定（规则 key → 结果）→ 各适用规则里最好的一档评级。

    终局判定是按规则逐条给出的，没有单一"本件评级"；统计侧关心的是这件
    装备最终成没成，所以取适用规则里的最高档。跳过/不适用的规则不参与。
    返回 ascii 档位 key，无可用规则时返回 None（该字段选填）。
    """
    from lvjiang.apps.yysls.core.tuning_rules.models import RATING_KEYS
    from lvjiang.apps.yysls.telemetry import vocab as telemetry_vocab

    best = -1
    for r in (judgement or {}).values():
        if not isinstance(r, dict) or r.get("skipped") or r.get("not_applicable"):
            continue
        key = telemetry_vocab.normalize_rating(r.get("rating"))
        if key in RATING_KEYS:
            best = max(best, RATING_KEYS.index(key))
    return RATING_KEYS[best] if best >= 0 else None


class AutoTuningWorkflow(TuningContextMixin, BaseWorkflow):
    """自动调律工作流"""

    # 脚本元数据（供发现层暴露到日常下拉与设备端悬浮面板）。
    # 暂不定义 PARAMETERS：本流程的配置面（部位多选 + 每规则玩法多选 + 全局开关）
    # 超出现有 select/number 参数 schema 的表达力，设备端先按内置默认配置运行
    # （全部部位 + 全部规则默认判定，见 _ensure_judge_config 的回退），
    # 细粒度配置仍只在桌面调律 Tab 经 run_ctx 注入。
    DISPLAY_NAME = "自动调律"
    # 专用脚本：日常 Tab 不画其参数面板，由专属配置页自行管理
    SCOPE = "dedicated"

    # ─── 共享常量 ──────────────────────────────────────────

    WEAPON_SLOTS = ["main_weapon", "sub_weapon", "ring", "pendant"]
    ARMOR_SLOTS = ["head", "chest", "leg", "wrist"]
    # 有类型分组的部位（武库装备穿插时不能结束扫描）
    GROUPED_SLOTS = {"main_weapon", "sub_weapon"}
    WEAPON_DETAIL = "equip_weapon_detail"
    ARMOR_DETAIL = "equip_armor_detail"
    EQUIP_DETAIL = "equip_detail"  # 通用交互区域（more_func/sub_func_*/recycle_*）

    SCAN_FIELDS = [
        "equip_type", "equip_level", "base_attr",
        "affix_gong", "affix_shang", "affix_jue",
        "affix_zhi", "affix_yu",
        "status",
    ]

    GRID_SCENE = "bag_equip_detail"
    GRID_PANEL = "bag_grid"

    TUNE_SCENE = "equip_tune_detail"
    # 调律结果弹窗已并入调律页（result 视图），运行时同场景寻址
    RESULT_SCENE = "equip_tune_detail"
    MATERIAL_PANEL = "materials"  # 材料区 panel key（7 列 1 行 grid）
    MATERIAL_GROUP = "调律材料"
    MAX_AFFIX = 5

    # 部位 key → 中文名（与 hub 共享，避免重复定义）
    _SLOT_NAMES = SLOT_LABELS

    # ─── 编排层状态 ────────────────────────────────────────

    # 调律功能组件（懒初始化）──────────────────────────────

    _judge: TuningJudge | None = None
    _executor: TuningExecutor | None = None
    _navigator: TuningNavigator | None = None
    _recycler: TuningRecycler | None = None
    _resetter: TuningResetter | None = None
    _recorder: TuningRecorder | None = None
    _route_strategy: TuningRouteStrategy | None = None
    _run_state: TuningRunState | None = None
    _equipment_session: EquipmentSession | None = None

    @property
    def run_state(self) -> TuningRunState:
        if self._run_state is None:
            self._run_state = TuningRunState()
        return self._run_state

    @property
    def equipment_session(self) -> EquipmentSession:
        if self._equipment_session is None:
            self._equipment_session = EquipmentSession()
        return self._equipment_session

    @property
    def route_strategy(self) -> TuningRouteStrategy:
        """本次工作流共享的平台路径策略。"""
        if self._route_strategy is None:
            self._route_strategy = create_tuning_route_strategy(self)
        return self._route_strategy

    @property
    def judge(self) -> TuningJudge:
        if self._judge is None:
            self._judge = TuningJudge(self.ctx)
        return self._judge

    @property
    def executor(self) -> TuningExecutor:
        if self._executor is None:
            self._executor = TuningExecutor(self)
        return self._executor

    @property
    def navigator(self) -> TuningNavigator:
        if self._navigator is None:
            self._navigator = TuningNavigator(self, self.route_strategy)
        return self._navigator

    @property
    def recycler(self) -> TuningRecycler:
        if self._recycler is None:
            self._recycler = TuningRecycler(self, self.route_strategy)
        return self._recycler

    @property
    def resetter(self) -> TuningResetter:
        if self._resetter is None:
            self._resetter = TuningResetter(self, self.route_strategy)
        return self._resetter

    @property
    def recorder(self) -> TuningRecorder:
        if self._recorder is None:
            self._recorder = TuningRecorder()
        return self._recorder

    def _emit_progress(self, signal_name: str, *args):
        """经 engine 向 UI 发送进度信号；异常时静默跳过（测试/设备端兼容）"""
        try:
            hub = getattr(self.engine, '_progress_hub', None) if self.engine else None
            if hub is not None:
                getattr(hub, signal_name).emit(*args)
        except Exception:
            pass  # 进度信号失败不应影响工作流执行

    def _emit_operation(self, phase: str, message: str, **details) -> None:
        """在耗时动作开始前推送当前阶段，避免进度页只显示事后结果。"""
        self._emit_progress("operation_updated", {
            "phase": phase,
            "message": message,
            **details,
        })

    def _emit_equip_finish(self, name, equip_data, rounds=0,
                           affix_count=0, status="skipped",
                           final_rating="", reason=""):
        """发送装备处理结束信号（供 early-return 路径补发）"""
        self._emit_progress("equipment_finished", {
            "name": name,
            "final_rating": final_rating,
            "rounds": rounds,
            "affix_count": affix_count,
            # equip_data 可为 None：武库装备刻意不建 EquipmentData
            # （判定只依赖 status 原始 OCR，不依赖装备解析成功）
            "final_affixes": ([a.to_dict() for a in equip_data.affixes]
                              if equip_data is not None else []),
            "status": status,
            "reason": reason,
        })

    def _emit_equip_started(self, name: str, equip: dict,
                            equip_data: EquipmentData) -> None:
        """OCR 解析成功即展示装备，不让潜力判定或提前返回阻塞面板。"""
        target_names: set[str] = set()
        for cfg in (self.ctx.judge_configs or {}).values():
            target_names.update(cfg.get("affix_pool") or [])
        self._emit_progress("equipment_started", {
            "name": name,
            "type": equip.get("type", ""),
            "level": equip.get("level", 0),
            "quality": equip.get("quality", ""),
            "affixes": [a.to_dict() for a in equip_data.affixes],
            "expect_rating": "",
            "target_affixes": sorted(target_names),
        })

    def _emit_assessment(self, potential: dict, expect: str | None,
                         stage: str = "scan") -> None:
        self._emit_progress("equipment_assessed", {
            "expect_rating": expect or "",
            "rule_ratings": potential,
            "stage": stage,
        })

    def _recycle_current(self, equip_data: EquipmentData, detail_scene: str,
                         stage: str, reason: str,
                         report: dict | None = None) -> RecycleOutcome:
        outcome = self.recycler.recycle_current(
            equip_data, detail_scene, stage, reason, report)
        if outcome is RecycleOutcome.LOCKED:
            fp = self._make_fingerprint(equip_data.to_dict())
            self.run_state.record_locked(fp)
        return outcome

    # ─── 生命周期 ─────────────────────────────────────────

    @property
    def is_stopped(self) -> bool:
        """停止请求或材料耗尽（大律准石低于基准）都视为停止，
        背包遍历与部位循环全部收束，复用用户中断的退出路径。

        super().is_stopped 放在前面求值：它内部会先过暂停检查点
        （_wait_if_paused），若放在 or 后面，一旦材料耗尽，短路求值会
        跳过暂停阻塞——用户此时按暂停热键会被静默吞掉，直接当结束处理。
        """
        return super().is_stopped or self.executor.materials_exhausted

    @property
    def slot_level_exhausted(self) -> bool:
        """当前部位已命中无需继续遍历的槽位级终止条件。"""
        return bool(getattr(self, "_slot_level_exhausted", False))

    def _mark_non_weapon_wuku_bottom(self, is_wuku: bool,
                                     name: str = "") -> bool:
        """非武器部位读到武库标记时，立即结束该部位遍历。

        这是槽位级边界，不是装备处置规则。调用方必须在进入评级、调律、
        回收等单件处理前检查 ``slot_level_exhausted``。
        """
        slot = getattr(self, "_current_slot", "")
        if not is_wuku or not slot or slot in self.GROUPED_SLOTS:
            return False
        if not self.slot_level_exhausted:
            logger.info("  检测到武库装备，当前非武器部位已到底")
            # 面板要看得见：否则用户只会看到扫描毫无征兆地停下。
            # 只在首次置位时发，避免同一部位重复刷屏。
            self._emit_equip_finish(
                name or tr("武库装备"), None,
                status="wuku_bottom", reason=tr("武库装备，到达部位边界"))
        self._slot_level_exhausted = True
        return True

    @staticmethod
    def _missing_output_fields() -> list[str]:
        """当前图库空间缺失的必需输出字段 key（调律启动预检）"""
        from lvjiang.apps.yysls.core.recognizer.reference_adapter import (
            get_missing_output_fields,
        )
        from lvjiang.core.reference_db import ReferenceDatabase
        db = ReferenceDatabase()
        db.load()
        return get_missing_output_fields(db)

    def run(self) -> dict:
        # 进度桥属于 UI 适配层，由 tuning_tab.configure 注入；日常、批量和
        # 无界面运行没有进度消费者时保持为空，工作流不得反向导入 PyQt。
        # 预检：当前图库空间必须满足调律输出字段契约（levels/counts）
        missing = self._missing_output_fields()
        if missing:
            msg = (f"当前图库空间缺少输出字段 {'、'.join(missing)}，"
                   f"无法启动自动调律（请在图库元数据定义中补充）")
            logger.error(msg)
            self._emit_progress("tuning_finished", {
                "total_equipment": 0, "total_rounds": 0, "interrupted": False,
            })
            return {"error": msg}

        try:
            self.executor.reset_state()
            self._run_state = TuningRunState()
            self._equipment_session = None
            self._equipment_detail_open = False
            self._ensure_judge_config()
            group = self._ensure_base_group()
            self.recorder.reset()  # 重置所有记录状态
            # 各槽位已装备装备信息（进入时读取）；值为 None 表示类型
            # 未解析出来，该部位退化为按部位终止语义（见 _read_equipped）
            self._equipped_items: dict[str, dict | None] = {}
            # 加载导航所需的 DSL subcall 文件（每次运行都重新加载，保证修改立即生效）
            self.navigator.load_dependencies()
            selected = self._resolve_selected_slots()
        except ValueError as e:
            msg = str(e)
            logger.error(msg)
            self._emit_progress("tuning_finished", {
                "total_equipment": 0, "total_rounds": 0, "interrupted": False,
            })
            return {"error": msg}
        logger.info(f"基础规则组: {group.name}")

        self._open_doc(selected)
        try:
            if not self.navigator.navigate_to_equip():
                msg = "未能进入背包装备页，自动调律中止"
                logger.error(msg)
                self.output["error"] = msg
                return self.output

            first_slot = True
            _batch_total_slots = len(selected)
            _slot_idx = 0
            for slot in selected:
                if self.is_stopped:
                    break
                detail = (self.WEAPON_DETAIL if slot in self.WEAPON_SLOTS
                          else self.ARMOR_DETAIL if slot in self.ARMOR_SLOTS
                          else None)
                if detail is None:
                    continue

                _slot_idx += 1
                self._emit_progress("batch_progress", {
                    "current_slot": slot,
                    "slot_index": _slot_idx,
                    "total_slots": _batch_total_slots,
                })
                if first_slot and self.ctx.target_cell:
                    # 指定调律：只处理这一件即收工
                    self._process_slot_group_enter(slot)
                    r, c = self.ctx.target_cell
                    self._process_single_target(detail, r, c)
                    break

                if first_slot and self.ctx.skip_start:
                    # 初始跳过：先滚到指定行，再正常遍历
                    self._process_slot_group_enter(slot)
                    self._scroll_to_row(self.ctx.skip_start[0])
                    self._traverse_bag(detail)
                else:
                    self._process_slot_group(slot, detail)

                first_slot = False

            # 区分用户中断和材料耗尽：前者保留页面，后者返回主页
            user_interrupt = (hasattr(self, '_stop_check')
                             and callable(self._stop_check)
                             and self._stop_check())
            if user_interrupt:
                # 先封存当前装备、写完 Markdown 调律报告并关闭文件，随后才
                # 对 UI 宣告流程结束；避免“已结束”时报告仍缺最后一件。
                finalized = self.recorder.finalize_interrupted_current()
                if finalized:
                    partial = self.recorder.collect_reports()[-1]
                    self._emit_progress("equipment_finished", {
                        "name": partial.get("name", ""),
                        "final_rating": "",
                        "rounds": partial.get("rounds", 0),
                        "affix_count": partial.get("final_affix_count", 0),
                        "final_affixes": partial.get("final_affixes", []),
                        "status": "interrupted",
                    })
                self.output["tuning_reports"] = list(
                    self.recorder.collect_reports())
                if "stop_reason" not in self.output:
                    self.output["stop_reason"] = "用户中断"
            self._close_doc()
            # 进度信号：整体完成
            self._emit_progress("tuning_finished", {
                "total_equipment": _slot_idx,
                "total_rounds": sum(
                    int(r.get("rounds") or 0)
                    for r in self.recorder.collect_reports()
                    if r.get("status") in ("tuned", "interrupted")),
                "interrupted": user_interrupt,
            })
            if user_interrupt:
                # 用户中断：保留当前页面，方便查看停在哪里
                logger.info("自动调律被用户中断，保留当前页面")
            else:
                # 正常完成或材料耗尽：返回主页
                if not self.navigator.navigate_back():
                    msg = "自动调律已完成，但未能返回主页"
                    logger.error(msg)
                    self.output["error"] = msg
                if self.executor.materials_exhausted:
                    logger.info("自动调律因材料耗尽而结束，已返回主页")
                else:
                    logger.info("自动调律完成，已返回主页")
        finally:
            self._close_doc()
        return self.output

    # ─── 调律说明文档（可交付阅读的叙事输出）──────────────

    def _open_doc(self, slots: list[str]):
        """创建本次运行的说明文档并写文档头；失败只警告不中断流程。

        操作用户名取引擎启动时绑定的 run_username；输出目录可用
        ctx.doc_dir 覆盖（供测试），缺省 logs/tuning/。
        """
        username = (self.engine.run_username if self.engine else "") or "default"
        try:
            doc = TuningDocWriter(username, self.ctx.doc_dir)
        except OSError as e:
            logger.warning(f"调律说明文档创建失败，本次不生成说明: {e}")
            self.recorder.set_doc(None)
            return
        self.recorder.set_doc(doc)
        # 各规则配置的开关聚合（any 语义），再映射为显示名 → 状态
        merged: dict[str, bool] = {}
        for cfg in (self.ctx.judge_configs or {}).values():
            for k, v in (cfg.get("switches") or {}).items():
                merged[str(k)] = merged.get(str(k), False) or bool(v)
        try:
            names = get_tune_config().switches
        except Exception:
            names = {}
        switches = {names.get(k, k): v for k, v in merged.items()}
        self.recorder.doc_start_run(
            username, self._describe_rules(), slots, switches)
        logger.info(f"调律说明文档: {doc.path}")

    def _describe_rules(self) -> list[str]:
        """由判定配置生成规则描述：显示名（玩法：勾选项）"""
        return TuningRecorder.describe_rules(self.ctx.judge_configs or {})

    def _close_doc(self):
        """写运行小结（含用户中断标记）+ 成品清单并关闭文档"""
        rec = self.recorder
        if rec.doc is None:
            return
        reports = rec.collect_reports()
        tuned = [r for r in reports if r.get("status") == "tuned"]
        processed = [r for r in reports
                     if r.get("status") in ("tuned", "interrupted")]
        total_rounds = sum(int(r.get("rounds") or 0) for r in processed)
        user_interrupt = (hasattr(self, '_stop_check')
                          and callable(self._stop_check)
                          and self._stop_check())
        rec.doc_end_run(user_interrupt, len(processed), total_rounds)
        rec.doc_run_summary(TuningRecorder.summary_items(tuned))
        rec.close_doc()

    # ─── 部位处理 ──────────────────────────────────────────

    def _read_equipped(self, slot: str) -> dict | None:
        """读取当前槽位已装备的装备信息。

        点击部位标签后详情页已打开，直接 OCR 即可。
        武器用 weapon_detail，防具/首饰用 armor_detail。

        返回 None 表示**没读出可用的类型**。这份数据唯一的用途是武器分组
        判定（武库/低等级装备是否与当前武器同类型），拿不到类型时不能假装
        「不同类型」——那会把一次 OCR 抖动误判成「该类型组已到底」，静默
        砍掉整个部位后面所有装备。调用方据此退化为旧的按部位终止语义。
        """
        detail_scene = (self.WEAPON_DETAIL if slot in self.WEAPON_SLOTS
                        else self.ARMOR_DETAIL)
        fields = self.SCAN_FIELDS + (["base_attr_2"]
                                     if detail_scene == self.ARMOR_DETAIL else [])
        raw = self.ocr_scene(detail_scene, fields)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        state = self._equipment_read_state(equip)
        if state != "empty":
            self._equipment_detail_open = True
            self._close_equipment_detail()
        if state != "valid":
            logger.error(
                f"槽位 {slot} 已装备装备解析失败（raw={raw!r}），"
                f"level={equip.get('level')!r}, type={equip.get('type')!r}："
                f"该部位退化为按部位终止语义，不再做武器分组判定")
            return None
        return equip

    def _grouped_type_matches(self, slot: str,
                              current_type: str | None) -> bool | None:
        """当前装备类型是否与该槽位已装备的一致（仅用于分组部位）。

        None = 类型未知（进入槽位时解析失败，或当前装备没解析出类型）。
        调用方必须把 None 与 False 区别对待：False 是「确实是别的类型，
        该类型组已到底」，None 只是「不知道」，不能据此声称到底。
        """
        equipped = self._equipped_items.get(slot)
        if equipped is None:
            return None
        equipped_type = equipped.get("type")
        if not equipped_type or not current_type:
            return None
        return current_type == equipped_type

    def _process_slot_group_enter(self, slot: str):
        """点击部位标签进入背包网格页

        切换部位必然伴随窗口内装备整体更新，上一部位记录的锁定
        阻断指纹对新部位无效，必须清空。
        """
        self._current_slot = slot
        self._slot_level_exhausted = False
        self.run_state.enter_slot(slot)
        logger.info(f"── 处理部位: {slot} ──")
        self._emit_progress(
            "slot_entered", slot,
            self._SLOT_NAMES.get(slot, slot))
        self.click_region(self.GRID_SCENE, slot)
        self.wait_stable("page_refresh")  # 背包浏览页 → 装备详情页
        # 读取当前槽位已装备装备信息（用于分组部位的类型过滤）
        self._equipped_items[slot] = self._read_equipped(slot)

    def _process_slot_group(self, slot: str, detail_scene: str):
        self._process_slot_group_enter(slot)
        self._traverse_bag(detail_scene)

    def _scroll_to_row(self, target_row: int) -> None:
        """滚动背包网格使 target_row 出现在可见区第一行

        每次 drag_grid("up") 步进一行。target_row <= 1 时无操作。
        """
        if target_row <= 1:
            return
        drags = target_row - 1
        logger.info(f"滚动定位: 目标行={target_row}, 需拖拽{drags}次")
        for _i in range(drags):
            if self.is_stopped:
                break
            self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", hold=0.3)
            self.wait_stable("scroll_settle")  # 滚动后面板稳定
        self.align_panel(self.GRID_SCENE, self.GRID_PANEL)

    def _process_single_target(self, detail_scene: str, row: int, col: int) -> None:
        """指定调律：定位到 (row, col)，只处理这一件，含完整处理链"""
        self._scroll_to_row(row)
        if self.is_stopped:
            return
        # 滚动后目标行在可见区第 1 行
        name, fp, equip = self._read_row(detail_scene, 1, col)
        if self.slot_level_exhausted:
            self._close_equipment_detail()
            return
        if not fp:
            logger.warning(f"指定调律: 目标格 ({row},{col}) 为空，无装备可处理")
            return
        if self._equipment_read_state(equip) != "valid":
            return
        logger.info(f"指定调律: 定位到 ({row},{col})，装备={name or fp}")
        self._process_equipment(name, equip, detail_scene, row=1, col=col)

    # ─── 背包遍历（滚动策略实现见 bag_traversal）──────────────

    def _click_grid(self, row: int, col: int) -> bool:
        """按对齐结果点击背包网格中心。"""
        return self.click_panel(self.GRID_SCENE, self.GRID_PANEL, row, col)

    def _read_row(self, detail_scene: str, row: int,
                  col: int = 1) -> tuple[str, str, dict]:
        """点击指定行/列 slot，等面板刷新后 OCR，返回 (装备名, 指纹, 装备dict)

        默认第 1 列（行指纹/滚动校验链路）；列遍历时传入 col 读非首列。"""
        self._click_grid(row, col)
        self.wait_stable("page_refresh")  # 点击装备 → 详情页加载
        fields = self.SCAN_FIELDS + (["base_attr_2"]
                                     if detail_scene == self.ARMOR_DETAIL else [])
        raw = self.ocr_scene(detail_scene, fields)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        state = self._equipment_read_state(equip)
        if state != "empty":
            self._equipment_detail_open = True
        # 武库标记：status 字段含“武库”说明该装备存放在武库中
        is_wuku = bool(raw and "武库" in (raw.get("status") or ""))
        if equip:
            equip["is_wuku"] = is_wuku
        # 非武器一旦出现武库装备即代表当前部位见底。该判断只依赖 status
        # 原始 OCR，不依赖装备名称/类型/词条是否成功解析。
        name = (equip.get("name") or "") if equip else ""
        self._mark_non_weapon_wuku_bottom(is_wuku, name)
        fp = self._make_fingerprint(equip)
        if state == "invalid":
            logger.error(
                f"  grid[{row}][{col}] 装备解析异常："
                f"level={equip.get('level')!r}, type={equip.get('type')!r}, "
                f"raw={raw!r}；跳过本件")
            # 不得让“存在但字段不完整”的装备以空指纹冒充空槽，否则首列
            # 会让整个背包提前到底。内部锚点只供遍历去重，不进入装备业务。
            fp = self._make_invalid_read_fingerprint(equip, raw)
            self._close_equipment_detail()
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
            if self.slot_level_exhausted:
                self._close_equipment_detail()
                break
            if not fp:
                logger.info(
                    f"  行{logical_row} grid[{win_row}][{col}] 空 slot → 本行到此为止")
                break
            if self._equipment_read_state(equip) != "valid":
                col += 1
                continue
            logger.info(f"  行{logical_row} grid[{win_row}][{col}] {name} fp={fp}")
            self.run_state.last_processing_result = None
            self._process_equipment(name, equip, detail_scene,
                                    row=win_row, col=col)
            if self.slot_level_exhausted:
                logger.info(
                    f"  当前部位已进入低等级区间，停止读取行{logical_row}后续装备")
                break
            process_result = self.run_state.last_processing_result
            slot_changed = (
                process_result.slot_changed if process_result is not None
                else self.recorder.equipment_recycled
            )
            if slot_changed:
                # 回收使后续装备前移补位到当前列，留在当前列继续处理
                if not fp:
                    break   # 安全兆底：回收后同列读空 → 本行结束
                continue
            col += 1

    def _traverse_bag(self, detail_scene: str):
        """按配置选择遍历策略并执行（策略实现见 bag_traversal）

        优先级：注入配置 ctx.scroll_strategy > 统一存储 wf_configs 的
        scroll_strategy；空串 = 默认策略；未知策略抛异常。
        """
        key = self.ctx.scroll_strategy or ""
        if not key:
            from lvjiang.core.config.wf_configs import get_wf_config
            key = get_wf_config("auto_tuning").get("scroll_strategy", "")
        if key and key not in TRAVERSALS:
            raise ValueError(f"未知遍历策略 '{key}'，请检查配置")
        if not key:
            key = DEFAULT_TRAVERSAL
        logger.info(f"背包遍历策略: {key}")
        TRAVERSALS[key]().traverse(self, detail_scene)

    # ─── 单件装备处理（潜力判定 + 实际调律 + 回报）────────

    def _process_equipment(self, name: str, equip: dict, detail_scene: str,
                           row: int | None = None, col: int = 1) -> str:
        """兼容遍历协议的字符串返回入口。

        新代码使用结构化 EquipmentProcessingResult；现有 BagTraversal 仍以
        指纹字符串为协议，待遍历 Port 迁移时再移除此兼容外壳。
        """
        try:
            result = self._process_equipment_result(
                name, equip, detail_scene, row=row, col=col)
            self.run_state.last_processing_result = result
            # 迁移期兼容测试替身和旧遍历扩展；生产决策已读取结构化结果。
            self.recorder.equipment_recycled = result.slot_changed
            return result.fingerprint
        finally:
            # 仅关闭仍然存在的装备详情。回收成功会清除 open 标记；空槽和
            # 完全不可理解的点击从不设置该标记。
            self._close_equipment_detail()

    def _process_equipment_result(
        self, name: str, equip: dict, detail_scene: str,
        row: int | None = None, col: int = 1,
    ) -> EquipmentProcessingResult:
        """单件处理入口：回收后就地重读同格，续处理补位装备。

        row/col 为该件在背包窗口中的格位；回收使后续装备前移补位，
        有格位时重读同格续处理（保险丝防死循环）；row=None 表示调用方
        无格位信息，回收后无法回读，返回空指纹由上层按空 slot 处理。
        返回最终占位装备的（终态）指纹，供上层更新行指纹。
        返回结构化格位影响，供列遍历判断是否留在原列处理补位装备。
        """
        fp, outcome = self._process_equipment_once(name, equip, detail_scene)
        if outcome is not None and outcome.detail_closed:
            self._equipment_detail_open = False
        if outcome is not RecycleOutcome.RECYCLED:
            return EquipmentProcessingResult(fp, recycle_outcome=outcome)
        if row is None:
            return EquipmentProcessingResult(
                "", SlotEffect.REMOVED, outcome)
        # 回收补位循环上限
        max_recycles = self.base_group.scan.max_consecutive_recycles
        for _ in range(max_recycles):
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, row, col)
            if self.slot_level_exhausted:
                return EquipmentProcessingResult(
                    fp, SlotEffect.REMOVED, outcome)
            if not fp:
                logger.info(f"  grid[{row}][{col}] 回收后已空，该格结束")
                return EquipmentProcessingResult(
                    "", SlotEffect.REMOVED, outcome)
            if self._equipment_read_state(equip) != "valid":
                return EquipmentProcessingResult(
                    fp, SlotEffect.REMOVED, outcome)
            logger.info(f"  回收补位 grid[{row}][{col}] → {name} fp={fp}")
            fp, outcome = self._process_equipment_once(name, equip,
                                                        detail_scene)
            if outcome is not None and outcome.detail_closed:
                self._equipment_detail_open = False
            if outcome is not RecycleOutcome.RECYCLED:
                return EquipmentProcessingResult(
                    fp, SlotEffect.REMOVED, outcome)
        return EquipmentProcessingResult(
            "", SlotEffect.REMOVED, outcome)

    def _process_equipment_once(
        self, name: str, equip: dict, detail_scene: str
    ) -> tuple[str, RecycleOutcome | None]:
        """对一件装备走完整链路：潜力判定 → 值得则调律到满 → 终局判定 → 回报。

        进入时已停在 bag_equip_detail（含 detail 区，_read_row 已点选该件）。
        未调律分支（already_full/junk_blank/no_tune_entry）保持在背包页；
        已调律分支调完后单次 back 返回背包浏览页，供遍历继续滚动。
        skip_tuning 测试开关只拦截「值得调律且已进调律页」的装备：
        词条已满/不值得/无入口仍走各自正常分支，不会统统进调律页。
        仅真正进过调律页的装备（tuned/skip_tuning）与异常的 no_tune_entry
        产出 report 追加进 output["tuning_reports"]；词条已满/垃圾胚子未做
        调律，不收集（一次遍历几百件时会淹没有效信息）。
        返回 (调律后终态指纹, 回收结果)；已回收时指纹为空串
        （该格已被补位装备占据，由 _process_equipment 重读）。
        """
        if not equip:
            return "", None

        # 正常路径会在 _read_row 返回后立即收束；这里保留旁路调用兜底。
        # 武库装备只承担槽位边界/分组边界语义，不得创建调律会话，更不能
        # 进入评级、调律或回收。
        if equip.get("is_wuku"):
            slot = getattr(self, "_current_slot", "")
            if self._mark_non_weapon_wuku_bottom(True):
                return self._make_fingerprint(equip), None
            if slot in self.GROUPED_SLOTS:
                match = self._grouped_type_matches(slot, equip.get("type"))
                if match:
                    logger.info(
                        f"  [{name}] 武库装备（同类型），跳过"
                        "（武器分组，不结束扫描）")
                    self._emit_equip_finish(
                        name, None, status="wuku_skip",
                        reason=tr("武库装备，同武器组，跳过"))
                else:
                    if match is None:
                        # 类型未知：退化为按部位终止，不谎称「不同类型」。
                        # 具体的解析失败已在 _read_equipped 里记了 error。
                        logger.warning(
                            f"  [{name}] 武库装备，槽位类型未知 → "
                            f"退化为按部位终止")
                    else:
                        logger.info(
                            f"  [{name}] 武库装备（不同类型），当前类型组已到底")
                    self._slot_level_exhausted = True
                    self._emit_equip_finish(
                        name, None, status="wuku_bottom",
                        reason=tr("武库装备，到达部位边界"))
                return self._make_fingerprint(equip), None
            logger.warning(f"  [{name}] 武库装备（当前部位未知），跳过")
            return self._make_fingerprint(equip), None

        equip_data = EquipmentData.from_dict(equip)
        self._equipment_session = EquipmentSession(
            name=name, equipment=equip_data)
        affix_count = int((equip.get("_extra") or {}).get("affix_count", 0))
        self._emit_equip_started(name, equip, equip_data)

        # 前置拦截 1：等级门槛 — 低于 min_level 直接跳过，不走任何判定
        level = equip.get("level")
        # UI 覆盖优先：ctx.min_level 不为 None 时覆盖基础规则的 scan.min_level
        min_level = (
            self.ctx.min_level if self.ctx.min_level is not None
            else self.base_group.scan.min_level
        )
        if (isinstance(level, (int, float)) and not isinstance(level, bool)
                and level > 0 and level < min_level):
            slot = getattr(self, "_current_slot", "")
            if slot in self.GROUPED_SLOTS:
                # 主/副武器按类型分组，检查装备类型是否与当前装备一致
                match = self._grouped_type_matches(slot, equip.get("type"))
                if match:
                    # 同类型低等级：同组内后面都是低的，跳过但不结束扫描
                    logger.info(
                        f"  [{name}] 等级 {level} < 门槛 {min_level}"
                        f"（同类型，跳过但不结束扫描）")
                    self._emit_equip_finish(
                        name, equip_data, affix_count=affix_count,
                        status="below_level_skip",
                        reason=f"等级 {level} < 门槛 {min_level}，跳过（同类型）")
                    return self._make_fingerprint(equip), None
                else:
                    # 不同类型低等级：已越过当前类型组，停止扫描。
                    # match is None（类型未知）同样落这里——退化为按部位
                    # 终止，与非分组部位的语义一致。
                    reason_cn = ("不同类型" if match is False else "槽位类型未知")
                    logger.info(
                        f"  [{name}] 等级 {level} < 门槛 {min_level}"
                        f"（{reason_cn}，当前类型组已到底）")
                    self._slot_level_exhausted = True
                    self._emit_equip_finish(
                        name, equip_data, affix_count=affix_count,
                        status="below_level",
                        reason=f"等级 {level} < 门槛 {min_level}，当前类型组已到底")
                    return self._make_fingerprint(equip), None
            logger.info(
                f"  [{name}] 等级 {level} < 门槛 {min_level}；背包装备按等级倒序，"
                "结束当前部位扫描")
            self._slot_level_exhausted = True
            self._emit_equip_finish(
                name, equip_data, affix_count=affix_count,
                status="below_level",
                reason=f"等级 {level} < 门槛 {min_level}，结束当前部位扫描")
            return self._make_fingerprint(equip), None

        # 前置拦截 1：品阶识别异常 — 无法识别品阶说明 OCR 有问题，直接跳过
        quality = equip.get("quality")
        if not quality:
            logger.warning(
                f"  [{name}] 品阶识别失败（quality 为空），视为异常直接跳过")
            self._emit_equip_finish(
                name, equip_data, affix_count=affix_count,
                status="invalid_quality", reason="品阶识别失败")
            return self._make_fingerprint(equip), None

        fp = self._make_fingerprint(equip_data.to_dict())
        if fp and self.run_state.is_locked(fp):
            logger.info(f"  [{name}] 本次运行已确认锁定，跳过重复回收")
            self._emit_equip_finish(
                name, equip_data, affix_count=affix_count,
                status="locked", reason="本次运行已确认锁定")
            return fp, None
        logger.info(
            f"  处理装备: {self._equip_label(equip)} "
            f"quality={equip.get('quality')} affix_count={affix_count}")
        self.recorder.start_report(name, equip, affix_count)

        # A. 词条已满 → 终局判定 + 扫描处理（已满不进调律，
        #    走 scan 行为点决定回收/保留）；未处理过，不收集 report
        if affix_count >= self.MAX_AFFIX:
            logger.info(f"  [{name}] 词条已满（{affix_count}），仅做终局判定")
            judgement = self.judge.final_judge(equip_data)
            actual_rating = self.judge.compute_actual_rating(equip_data) or ""
            self._emit_assessment(judgement, actual_rating)
            self.recorder.report_set("status", "already_full")
            self.recorder.report_set("final_judgement", judgement)
            outcome = self._on_scan_reject(
                equip_data, judgement, detail_scene, already_full=True)
            # 词条已满，计算实际评级用于进度显示
            if outcome is RecycleOutcome.RECYCLED:
                self._emit_equip_finish(name, equip_data, affix_count=affix_count,
                                        status="recycled",
                                        final_rating=actual_rating)
                self.recorder.discard_report()
                return "", outcome
            self._emit_equip_finish(name, equip_data, affix_count=affix_count,
                                    final_rating=actual_rating,
                                    status="already_full")
            self.recorder.discard_report()
            return self._make_fingerprint(equip_data.to_dict()), outcome

        # B. 进入决策（scan 行为点）：传入规则预期评级 ≥ 进入门槛
        #    → 进调律；未达门槛按扫描处置表决定回收/保留。
        #    未处理过，不收集 report，也不写说明文档
        scan_cfg = self.base_group.scan
        potential = judge_equipment_potential(
            equip_data, self.ctx.judge_configs, self.ctx.judge_rule_keys)
        _, logs = summarize_potential(potential)
        expect = self.judge.expect_key(potential)
        self.equipment_session.expected_rating = expect
        self._emit_assessment(potential, expect)
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        self.recorder.report_set("worthiness", logs)
        self._emit_operation(
            "scan", "潜力判定完成，正在匹配扫描处理规则",
            affix_count=affix_count, expect_rating=expect)
        # 重置调满后回收模式标记与强制调律标记（每件装备重新判断）
        self.equipment_session.mode = TuningMode.NORMAL
        if (expect is None or RATING_RANK[expect]
                < RATING_RANK[scan_cfg.entry_min_rating]):
            entry_label = RATING_LABELS.get(scan_cfg.entry_min_rating,
                                            scan_cfg.entry_min_rating)
            logger.info(f"  [{name}] 预期评级未达进入门槛"
                        f"（≥{entry_label}），不进调律")
            outcome = self._on_scan_reject(equip_data, potential,
                                            detail_scene)
            if outcome is RecycleOutcome.RECYCLED:
                self._emit_equip_finish(name, equip_data, affix_count=affix_count,
                                        status="recycled")
                self.recorder.discard_report()
                return "", outcome
            # 调满后回收模式：继续走调律流程
            if self.equipment_session.tune_full_recycle:
                logger.info(f"  [{name}] 进入调满后回收模式")
            elif self.equipment_session.force_tune:
                logger.info(f"  [{name}] 扫描处理强制调律，进入调律页")
            else:
                self._emit_equip_finish(name, equip_data, affix_count=affix_count)
                self.recorder.discard_report()
                return self._make_fingerprint(equip_data.to_dict()), outcome

        # C. 值得 → 实际调律；说明文档开装备节（只写命中的规则）
        self.recorder.doc_start_equipment(equip)
        self.recorder.doc_worthiness_matched(potential)
        self._emit_operation("navigation", "装备符合进入条件，正在打开调律页")
        if not self.navigator.nav_to_tune():
            logger.info(f"  [{name}] 未找到调律入口，跳过")
            self.recorder.doc_note("已符合规则但未找到调律入口，跳过本件")
            self.recorder.commit_report("no_tune_entry")
            self.output.setdefault("tuning_reports", []).extend(
                self.recorder.collect_reports()[-1:])
            self._emit_equip_finish(
                name, equip_data, affix_count=affix_count,
                status="no_tune_entry", reason="未找到调律入口")
            return self._make_fingerprint(equip_data.to_dict()), None

        # 临时测试开关：只有词条未满、判定值得调律且找到入口的装备
        # 才走到这里；真实进出调律页但不执行调律，供滚动遍历压测用。
        # 装备未被改动，不走垃圾/完成后处理挂载点。
        if self.ctx.skip_tuning:
            logger.info(f"  [{name}] 值得调律，但跳过实际调律（测试开关）")
            self.recorder.doc_note("值得调律，但测试开关已启用，跳过实际调律")
            self.navigator.leave_tune()
            self.recorder.commit_report("skip_tuning")
            self.output.setdefault("tuning_reports", []).extend(
                self.recorder.collect_reports()[-1:])
            self._emit_equip_finish(
                name, equip_data, affix_count=affix_count,
                status="skip_tuning", reason="测试开关已启用")
            return self._make_fingerprint(equip_data.to_dict()), None

        # 进入调律页成功，缓存材料区 OCR（后续轮次复用，避免每轮 OCR）
        self._emit_operation("material", "已进入调律页，正在读取材料库存")
        self.executor.cache_materials()

        # 结束处理支撑：首词条快照（重置后仅剩首词条）+ 本件重置计数
        tune_cfg = self.base_group.tune
        base_affixes = list(equip_data.affixes)
        resets_used = 0
        tune_recycle_reason = ""
        # 统计采集：一件装备一条事件，这里只开场，逐轮 record_roll 追加，
        # 离开调律页后 end_session 才落盘。探针内部有 @never_raises 护栏
        # + 未同意时早退，绝不中断调律主流程。
        telemetry_probe.begin_session(
            equip_data=equip_data, initial_affixes=base_affixes,
            rule_keys=self.ctx.judge_rule_keys)
        stop_key = "completed"

        # ── 初始判定（initial_check）：第一次调律前先执行一次结束处理 ──
        # 用于垃圾金装复用：先走结束处理重置清空词条，再开始正常调律
        skip_tune_loop = False
        stop_reason = ""
        if tune_cfg.initial_check and tune_cfg.enabled:
            logger.info(f"  ═══ [{name}] 初始判定（第一次调律前执行结束处理）═══")
            self._emit_operation(
                "decision", "执行首次调律前的初始判定",
                affix_count=affix_count, resets=resets_used)
            action, why, resets_used, affix_count = self._execute_end_processing(
                tune_cfg, equip_data, affix_count, resets_used, base_affixes,
                potential=potential, is_initial_check=True)
            self.recorder.doc_note(f"初始判定：{why}")
            if action == "continue":
                # 放行或重置成功，进入正常调律循环
                if resets_used > 0:
                    self.recorder.report_set("resets", resets_used)
            else:
                # skip 或 recycle → 结束调律循环
                skip_tune_loop = True
                if action == "recycle":
                    tune_recycle_reason = f"初始判定：{why}"
                stop_reason = f"初始判定：{why}"
                stop_key = "judged_before_tuning"
                self.recorder.report_set("stop_reason", stop_reason)

        rounds = 0
        stop_reason = stop_reason or ""
        last_food_reason = ""
        last_actual: str | None = None  # 缓存最后一轮的实际评级
        while not self.is_stopped and not skip_tune_loop:
            logger.info(f"  ═══ [{name}] 调律轮次 #{rounds + 1}"
                        f"（当前 {affix_count}/{self.MAX_AFFIX}）═══")
            self._emit_operation(
                "tuning", f"准备执行第 {rounds + 1} 轮调律"
                f"（当前 {affix_count}/{self.MAX_AFFIX}）",
                round_no=rounds + 1, affix_count=affix_count,
                resets=resets_used)
            self.wait_stable("page_refresh")
            result = self.executor.tune_once(
                equip_data, self.equipment_session.expected_rating,
                self.equipment_session.tune_full_recycle,
                round_no=rounds + 1)
            if result is None:
                stop_reason = self.executor.abort_reason or "无法继续调律"
                stop_key = "cannot_continue"
                self.recorder.report_set("stop_reason", stop_reason)
                self.recorder.doc_note(f"{stop_reason}，结束调律")
                break
            rounds += 1
            affix_count += 1
            # 每轮结果挂在本件 report 下，与装备一一对应
            self.recorder.report_append("tune_results", result)
            _affix_count_before = len(equip_data.affixes)
            new_affix = self.navigator.collect_new_affix(
                equip_data, result.get("tune_affix", "")) \
                or result.get("tune_affix", "")
            # 统计采集：解析失败（equip_data.affixes 未增长）时传 None，
            # 探针据此作废**整条会话**——序列里挖个洞会让下游把第 4 轮
            # 误读成紧跟第 2 轮，条件概率直接算错。
            telemetry_probe.record_roll(
                new_affix=(equip_data.affixes[-1]
                          if len(equip_data.affixes) > _affix_count_before else None),
                slot=affix_count, resets=resets_used,
                food_label=self.executor.round_food)
            self.recorder.report_set("rounds", rounds)
            self.recorder.report_set("final_affix_count", affix_count)
            self.recorder.report_set(
                "latest_affixes", [a.to_dict() for a in equip_data.affixes])
            if self.executor.round_food_reason != last_food_reason:
                self.recorder.doc_food_strategy(self.executor.round_food_reason)
                last_food_reason = self.executor.round_food_reason
            self.recorder.doc_tune_round(rounds, self.executor.round_food,
                                         new_affix)
            # 刷新评级（每轮调律后词条变化，更新最大预期）
            incoming, new_expect = \
                self.judge.refresh_expectation(equip_data)
            self.equipment_session.expected_rating = new_expect
            # 实际评级仅在词条满 5 条后才有意义
            if affix_count >= self.MAX_AFFIX:
                last_actual = self.judge.compute_actual_rating(equip_data)
                actual_for_signal = last_actual
            else:
                actual_for_signal = None
            # 进度信号：单轮调律完成（含更新后的评级）
            self._emit_progress("tune_round_completed", {
                "round_no": rounds,
                "new_affix": new_affix,
                "food_used": self.executor.round_food,
                "food_reason": self.executor.round_food_reason,
                "material_stock": self.executor.get_material_stock(),
                "current_affixes": [a.to_dict() for a in equip_data.affixes],
                "affix_count": affix_count,
                "expect_rating": new_expect,
                "actual_rating": actual_for_signal,
                "rule_ratings": incoming,
            })
            # 扣减本轮狗粮缓存：返还时不消耗（不变），否则 -1
            if not self.executor.round_food_refunded:
                self.executor.decrement_food(self.executor.round_food)
            # 结束处理（tune 行为点）：调用公共结束处理逻辑
            action, why, resets_used, affix_count = self._execute_end_processing(
                tune_cfg, equip_data, affix_count, resets_used, base_affixes,
                is_initial_check=False,
                pre_incoming=incoming, pre_expect=new_expect)
            if action == "continue":
                # 放行或重置成功，继续调律循环
                if resets_used > 0:
                    self.recorder.report_set("resets", resets_used)
                    self.recorder.doc_note(f"重置调律（第 {resets_used} 次）：{why}")
                self.recorder.doc_round_decision(why)
                continue
            # 回收延到 back 回背包页后执行；skip = 结束保留
            if action == "recycle":
                tune_recycle_reason = why
            stop_key = {"recycle": "decided_recycle",
                        "skip": "decided_keep",
                        "tune_full_recycle": "tune_full_recycle"}.get(
                            action, "completed")
            stop_reason = why
            self.recorder.report_set("stop_reason", stop_reason)
            self.recorder.doc_round_decision(why)
            break

        # 返回背包浏览页，并由环境策略恢复装备详情页的可操作状态。
        self.executor.invalidate_cache()  # 退出调律页，清空材料缓存
        self._emit_operation("finish", "正在返回背包并完成当前装备收尾")
        self.navigator.leave_tune()

        judgement = self.judge.final_judge(equip_data)
        self.recorder.report_set("rounds", rounds)
        self.recorder.report_set("final_affix_count", affix_count)
        self.recorder.report_set("final_judgement", judgement)
        # 终态词条（含逐轮新增），供运行结束时的成品清单
        self.recorder.report_set("final_affixes",
                                 [a.to_dict() for a in equip_data.affixes])
        logger.info(f"  [{name}] 调律结束：共 {rounds} 轮，词条 {affix_count}/{self.MAX_AFFIX}")
        if not stop_reason:
            stop_reason = ("用户中断" if self.is_stopped
                           else "调律结束")
            stop_key = "user_stopped" if self.is_stopped else "completed"
        telemetry_probe.end_session(
            # mode 在这里取：会话中途可能被判定切成调满回收/强制调律
            mode=self.equipment_session.mode.value,
            stop_reason=stop_key,
            final_rating=_best_rating(judgement),
            total_rounds=rounds, resets=resets_used)
        self.recorder.doc_finish_equipment(rounds, affix_count, stop_reason,
                                           judgement)
        # 命中回收的装备在回到背包页后执行（阻断时不回收）
        outcome = None
        if tune_recycle_reason and not self.is_stopped:
            report = self.recorder.current_report
            outcome = self._recycle_current(
                equip_data, detail_scene, "tune", tune_recycle_reason,
                report)
        self.recorder.commit_report("tuned")
        self.output.setdefault("tuning_reports", []).extend(
            self.recorder.collect_reports()[-1:])
        # 进度信号：装备处理结束
        # 实际评级仅在词条满 5 条后才有意义（复用循环中缓存的结果，避免重复计算）
        if affix_count >= self.MAX_AFFIX and last_actual is not None:
            actual_rating = last_actual or ""
        elif affix_count >= self.MAX_AFFIX:
            # 兆底：循环未执行但词条已满（路径 A 已单独处理，此处保险）
            actual_rating = self.judge.compute_actual_rating(equip_data) or ""
        else:
            actual_rating = ""
        self._emit_progress("equipment_finished", {
            "name": name,
            "final_rating": actual_rating,
            "rounds": rounds,
            "affix_count": affix_count,
            "final_affixes": [a.to_dict() for a in equip_data.affixes],
            "status": "recycled" if outcome is RecycleOutcome.RECYCLED else "done",
        })
        if outcome is RecycleOutcome.RECYCLED:
            return "", outcome
        return self._make_fingerprint(equip_data.to_dict()), outcome

    # ─── 结束处理公共逻辑（初始判定 + 每轮结束共用）──────────

    def _execute_end_processing(
        self, tune_cfg, equip_data: EquipmentData, affix_count: int,
        resets_used: int, base_affixes: list, potential: dict | None = None,
        is_initial_check: bool = False,
        pre_incoming: dict | None = None,
        pre_expect: str | None = None,
    ) -> tuple[str, str, int, int]:
        """执行结束处理逻辑（初始判定或每轮结束后调用）

        Args:
            tune_cfg: 调律行为配置
            equip_data: 装备数据
            affix_count: 当前词条数
            resets_used: 当前已重置次数
            base_affixes: 首词条快照（重置后仅剩首词条）
            potential: 初始判定时使用的潜力数据（仅初始判定需要）
            is_initial_check: 是否为初始判定模式

        Returns:
            (action, why, new_resets_used, new_affix_count):
            - action: "continue"=继续调律
                      "recycle"=回收该装备
                      "skip"=跳过/结束该装备
            - why: 最终原因
            - new_resets_used: 更新后的重置次数
            - new_affix_count: 更新后的词条数（重置后为1）
        """
        prefix = "初始判定" if is_initial_check else "结束处理"
        full = affix_count >= self.MAX_AFFIX

        # 调满后回收模式：跳过规则判定，调满即回收
        if self.equipment_session.tune_full_recycle and not is_initial_check:
            if full:
                why = "调满后回收模式：词条已满，执行回收"
                logger.info(f"  [{prefix}] {why}")
                return "recycle", why, resets_used, affix_count
            else:
                why = f"调满后回收模式：继续调律（{affix_count}/{self.MAX_AFFIX}）"
                logger.info(f"  [{prefix}] {why}")
                return "continue", why, resets_used, affix_count

        # 刷新预期评级（结束处理时更新，初始判定时使用传入的 potential）
        if is_initial_check:
            incoming = potential
        elif pre_incoming is not None:
            # 使用调用方预计算的结果（避免 tune_round_completed 信号前已跑过的判定重复执行）
            incoming = pre_incoming
            self.equipment_session.expected_rating = pre_expect
        else:
            incoming, self.equipment_session.expected_rating = \
                self.judge.refresh_expectation(equip_data)

        # 纯策略层只返回动作和值对象；本方法负责执行副作用。
        decision = self.judge.decide_end(
            tune_cfg, equip_data, incoming, full=full)
        action, why = decision.action, decision.reason
        logger.info(f"  [{prefix}] {why}")
        self._emit_operation(
            "decision", f"{prefix}命中：{action}", reason=why,
            action=action, affix_count=affix_count, resets=resets_used)

        # 处理各动作
        if action is BehaviorAction.CONTINUE:
            return action.value, why, resets_used, affix_count

        if action is BehaviorAction.RESET:
            # 调用重置公共逻辑
            reset_action, why, resets_used = self._execute_reset_action(
                tune_cfg, equip_data, resets_used, why, prefix=prefix)
            if reset_action == "continue":
                # 重置成功，更新装备状态
                equip_data.affixes = base_affixes[:1]
                equip_data.extra_data["affix_count"] = 1
                # 重置后刷新预期评级（首词条不变，但词条数变化可能影响判定）
                incoming, self.equipment_session.expected_rating = \
                    self.judge.refresh_expectation(equip_data)
                self._emit_assessment(
                    incoming, self.equipment_session.expected_rating, stage="scan")
                return "continue", why, resets_used, 1
            # skip 或 recycle → 直接返回
            return reset_action, why, resets_used, affix_count

        if action is BehaviorAction.TUNE_FULL_RECYCLE:
            # 结束处理命中后进入与扫描处理相同的直通模式：后续轮次
            # 不再做规则判定/狗粮决策，调满后回到背包执行回收。
            self.equipment_session.mode = TuningMode.TUNE_FULL_RECYCLE
            self._emit_progress("scan_decision", {
                "name": equip_data.name or equip_data.type,
                "action": "tune_full_recycle",
                "reason": f"{why}（调满后回收模式）",
            })
            if full:
                return "recycle", f"{why}（词条已满，执行回收）", resets_used, affix_count
            return "continue", f"{why}（进入调满后回收模式）", resets_used, affix_count

        # recycle 或 skip → 直接返回
        return action.value, why, resets_used, affix_count

    # ─── 重置调律公共逻辑（等级配置检查 + 重置执行）──────────

    def _execute_reset_action(
        self, tune_cfg, equip_data: EquipmentData, resets_used: int,
        why: str, prefix: str = "结束处理"
    ) -> tuple[str, str, int]:
        """执行重置调律的公共逻辑（等级配置检查 + 重置执行）

        Args:
            tune_cfg: 调律行为配置
            equip_data: 装备数据
            resets_used: 当前已重置次数
            why: 触发重置的原因
            prefix: 日志前缀（"初始判定" 或 "结束处理"）

        Returns:
            (action, why, new_resets_used):
            - action: "continue"=重置成功继续调律
                      "skip"=跳过该装备
                      "recycle"=回收该装备
            - why: 最终原因
            - new_resets_used: 更新后的重置次数
        """
        # 等级配置检查：该等级是否允许重置
        game_cfg = get_game_config()
        level_cfg = game_cfg.level_config_for(equip_data.level or 0)
        if level_cfg is None:
            # 等级配置不存在 → 异常，跳过该装备
            logger.error(
                f"  [{prefix}] 等级配置缺失（level={equip_data.level}），"
                "视为异常，跳过该装备")
            return "skip", f"等级配置缺失（level={equip_data.level}）", resets_used
        if level_cfg.allow_reset is False:
            # 该等级不支持重置 → 视为重置次数用尽
            action = tune_cfg.reset_exhausted_action
            why = (f"等级 {equip_data.level} 不支持重置"
                   f"（视为重置次数用尽）转{action}")
            logger.info(f"  [{prefix}] {why}")
            return action, why, resets_used
        # 等级配置允许重置，继续正常重置流程
        self._emit_operation(
            "reset", "规则已命中重置，正在检查次数、冷却和材料",
            reason=why, resets=resets_used)
        result = self.resetter.try_reset_tune(
            tune_cfg, resets_used, why,
            min_material_count=level_cfg.min_material_count)
        if result is True:
            # 重置成功，重新缓存材料区（重置后材料数量已变化）
            self.executor.cache_materials()
            self._emit_progress("equipment_reset", {
                "name": equip_data.name or equip_data.type,
                "type": equip_data.type,
                "level": equip_data.level,
                "quality": equip_data.quality,
                "before_affixes": [a.to_dict() for a in equip_data.affixes],
                "before_affix_count": len(equip_data.affixes),
                "after_affixes": [a.to_dict() for a in equip_data.affixes[:1]],
                "after_affix_count": min(len(equip_data.affixes), 1),
                "resets_used": resets_used + 1,
                # recorder 中的评级属于重置前装备；重置后等待重新判定。
                "before_rating": self.equipment_session.expected_rating or "",
                "expect_rating": "",
            })
            return "continue", why, resets_used + 1
        if isinstance(result, str):
            # 冷却期/材料不足拒绝 → 强制跳过（不走 reset_exhausted_action）
            return "skip", result, resets_used
        # 重置次数已用尽（本地上限或按钮无次数）→ 按配置转处置
        action = tune_cfg.reset_exhausted_action
        if action == "recycle":
            why = f"重置次数已用尽转回收（{why}）"
            logger.info(f"  [{prefix}] {why}")
        return action, why, resets_used

    # ─── 行为处置（按 tune_config.behavior 行为点配置驱动）──────

    def _on_scan_reject(self, equip_data: EquipmentData, potential: dict,
                        detail_scene: str | None = None,
                        already_full: bool = False) -> RecycleOutcome | None:
        """扫描阶段处置（scan 行为点）。

        两种入口：
        1. 未满装备预期评级未达进入门槛 → 按处置表决定回收/保留；
        2. 已满装备（already_full=True）→ 同走处置表，其中
           tune_full_recycle 视为直接回收（已满无需再调）。

        处置表评级按各规则自身判定语义懒取（incoming 复用进入
        决策的判定结果，all/custom 按需另跑潜力判定并缓存；
        逐规则声明的 first_affix_only 由评级提供者注入首词条）→
        处置表首条命中；仅 recycle 动作落地，tune_full_recycle
        设置标记后返回 None（继续走调律流程），其余保留。

        Returns:
            RECYCLED：已回收（该格已被补位装备占据）；
            LOCKED/UNAVAILABLE：尝试回收但装备仍保留；
            None：没有触发回收动作。
        """
        label = equip_data.name or equip_data.type
        cfg = self.base_group.scan
        if detail_scene is None or not cfg.enabled:
            logger.info(f"  [扫描处理] {label} 不进调律（处置未启用，保留）")
            self._emit_progress("scan_decision", {
                "name": label, "action": "kept", "reason": "处置未启用，保留"})
            return None
        decision = self.judge.decide_scan(cfg, equip_data, potential)
        action, why = decision.action, decision.reason
        if action is BehaviorAction.TUNE_FULL_RECYCLE:
            if already_full:
                # 已满装备无需再调，“调满后回收”意图等价于直接回收
                logger.info(f"  [扫描处理] {label} {why}（已满，直接回收）")
                self._emit_progress("scan_decision", {
                    "name": label, "action": "recycled",
                    "reason": f"{why}（已满，直接回收）"})
                return self._recycle_current(
                    equip_data, detail_scene, "scan", why)
            # 未满：设置标记，继续走调律流程
            logger.info(f"  [扫描处理] {label} {why}（调满后回收模式）")
            self.equipment_session.mode = TuningMode.TUNE_FULL_RECYCLE
            self._emit_progress("scan_decision", {
                "name": label, "action": "tune_full_recycle",
                "reason": f"{why}（调满后回收模式）"})
            return None
        if action is BehaviorAction.TUNE_THIS:
            # 强制调律：无视进入门槛，强制进入调律页
            # 配合结束处理「启用初始判定」实现调废装备重置复用
            logger.info(f"  [扫描处理] {label} {why}（强制调律模式）")
            self.equipment_session.mode = TuningMode.FORCE_TUNE
            self._emit_progress("scan_decision", {
                "name": label, "action": "force_tune",
                "reason": f"{why}（强制调律）"})
            return None
        if action is not BehaviorAction.RECYCLE:
            logger.info(f"  [扫描处理] {label} {why}")
            self._emit_progress("scan_decision", {
                "name": label, "action": "kept", "reason": why})
            return None
        self._emit_progress("scan_decision", {
            "name": label, "action": "recycled", "reason": why})
        return self._recycle_current(equip_data, detail_scene,
                                     "scan", why)

    # ─── 工具方法（bag_traversal 经 wf 调用）──────────────

    @staticmethod
    def _make_fingerprint(equip: dict) -> str:
        """获取或计算装备指纹。

        优先读取 dict 内已有的 _fp 字段（由 to_equipment / to_dict 自动设置），
        仅当缺失时才回退计算。type 未被装备解析器确认时必须返回空串，
        即使 OCR 噪声形成了 name 或上游错误缓存了 _fp；这是背包遍历判定
        空 slot 的入口隔离。
        """
        if not isinstance(equip, dict) or not equip.get("type"):
            return ""
        fp = equip.get("_fp", "")
        if fp:
            return fp
        from ...core.equip_parser.models import make_fingerprint
        return make_fingerprint(equip)

    @staticmethod
    def _equipment_read_state(equip: dict) -> str:
        """将 OCR 装备结果分为 empty / invalid / valid 三态。"""
        if not isinstance(equip, dict):
            return "empty"
        level = equip.get("level")
        has_level = (isinstance(level, (int, float))
                     and not isinstance(level, bool) and level > 0)
        has_type = bool(equip.get("type"))
        if not has_level and not has_type:
            return "empty"
        if not has_level or not has_type:
            return "invalid"
        return "valid"

    @staticmethod
    def _make_invalid_read_fingerprint(equip: dict, raw: dict) -> str:
        """为存在但不可理解的装备生成仅用于遍历的稳定锚点。"""
        payload = json.dumps(
            {"equip": equip, "raw": raw}, ensure_ascii=False,
            sort_keys=True, default=str).encode("utf-8")
        return "invalid:" + hashlib.sha1(payload).hexdigest()

    def _close_equipment_detail(self) -> None:
        """仅在确认当前存在装备详情时执行环境对应的关闭动作。"""
        if not getattr(self, "_equipment_detail_open", False):
            return
        self.route_strategy.close_equipment_detail()
        self._equipment_detail_open = False

    @staticmethod
    def _equip_label(equip: dict) -> str:
        """日志用装备标识：name|type（缺失部分省略，全空返回'空'）"""
        if not isinstance(equip, dict) or not equip:
            return "空"
        label = "|".join(
            str(p) for p in (equip.get("name"), equip.get("type")) if p)
        return label or "空"

    # ─── 判定配置兜底 ──────────────────────────────────────

    @property
    def base_group(self):
        """当前基础规则组（启动时经 run() 解析；未注入时惰性兜底）"""
        group = self.ctx.base_group
        return group if group is not None else self._ensure_base_group()

    def _ensure_base_group(self):
        """保证 ctx.base_group 可用。

        优先用启动时注入的规则组；未注入时回退读统一存储
        wf_configs["auto_tuning"].base_group；读不到或无效则抛异常。
        """
        group = self.ctx.base_group
        if group is not None:
            return group
        # 回退读统一存储的 base_group（日常 Tab 启动时 ctx 未注入）
        from lvjiang.core.config.wf_configs import get_wf_config
        group_key = get_wf_config("auto_tuning").get("base_group", "")
        if not group_key:
            raise ValueError(tr("未配置基础规则组，请在调律页选择基础规则组"))
        from lvjiang.apps.yysls.core.tuning_rules import get_tuning_group
        group = get_tuning_group(group_key)
        if group is None:
            raise ValueError(f"基础规则组 '{group_key}' 不存在，请检查配置")
        self.ctx.base_group = group
        return group

    def _ensure_judge_config(self):
        """保证 ctx.judge_configs/judge_rule_keys 可用。

        优先用 run_control 注入的实时 UI 配置；未注入时回退读统一存储
        wf_configs["auto_tuning"] 的 rules + switches；读不到则抛异常。
        """
        ctx = self.ctx
        if ctx.judge_configs is not None or ctx.judge_rule_keys is not None:
            return
        from lvjiang.core.config.wf_configs import get_wf_config
        tc = get_wf_config("auto_tuning")
        rules = tc.get("rules", {})
        if not rules:
            raise ValueError(tr("未配置调律规则，请在调律页选择至少一个规则"))
        switches = tc.get("switches", {})
        enabled = {k: {**cfg, "switches": switches}
                   for k, cfg in rules.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if not enabled:
            raise ValueError(tr("没有启用任何调律规则，请在调律页勾选至少一个规则"))
        ctx.judge_rule_keys = list(enabled)
        ctx.judge_configs = enabled

    def _resolve_selected_slots(self) -> list[str]:
        """调律部位：优先 UI 注入的 ctx.selected_slots；设备端经 task_runner
        启动时 ctx 未注入（selected_slots=None），回退读统一存储
        wf_configs["auto_tuning"].selected_slots；读不到则抛异常。"""
        selected = self.ctx.selected_slots
        if selected is None:
            from lvjiang.core.config.wf_configs import get_wf_config
            raw = get_wf_config("auto_tuning").get("selected_slots")
            if not isinstance(raw, list) or not raw:
                raise ValueError(tr("未配置调律部位，请在调律页选择至少一个部位"))
            valid = set(self.WEAPON_SLOTS) | set(self.ARMOR_SLOTS)
            selected = [s for s in raw if s in valid]
        if not selected:
            raise ValueError(tr("调律部位配置无效，请在调律页重新选择部位"))
        return selected
