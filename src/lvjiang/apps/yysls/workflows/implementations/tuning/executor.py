"""调律执行 — TuningExecutor

单轮调律操作：材料识别、狗粮决策、石头检查、就绪确认。
通过 wf 引用访问 UI 操作原语（click_region / ocr_scene 等）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.tuning_rules import (
    STONE_LABEL,
    FoodDecision,
    MaterialSettings,
)

from ......i18n import tr

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class TuningExecutor:
    """调律执行：单轮调律、材料检查、狗粮决策、就绪确认

    运行期状态（每次 run 重置）：
    - abort_reason: tune_once 返回 None 时的原因文案
    - round_food / round_food_reason: 本轮狗粮决策结果
    - materials_exhausted: 大律准石低于基准，全部退出
    - _material_cache: 材料区 OCR 缓存（进入调律页/重置后刷新）
    """

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf
        self.abort_reason = ""
        self.round_food = ""
        self.round_food_reason = ""
        self.round_food_refunded = False  # 本轮狗粮是否被概率返还
        self.materials_exhausted = False
        self._stone_check_waived = False
        self._tune_ready_waived = False
        self._material_cache: dict | None = None
        self._food_count_overrides: dict[str, int] = {}  # 狗粮数量覆盖（MaterialInfo.count 只读）

    def reset_state(self):
        """每次 run 开始时重置运行期状态"""
        self.materials_exhausted = False
        self._stone_check_waived = False
        self._tune_ready_waived = False
        self._material_cache = None
        self._food_count_overrides = {}

    def cache_materials(self):
        """进入调律页或重置后调用：OCR 材料区一次并缓存

        缓存大律准石、小律准石和三种狗粮的数值，后续调律轮次
        直接使用缓存，避免每轮重复 OCR。狗粮每轮 -1 由
        decrement_food() 维护；重置或退出调律页后需重新调用本方法刷新。
        """
        wf = self._wf
        settings = wf.base_group.materials
        if settings.stone_check_enabled or settings.food_rules:
            infos = wf.recognize_materials_info_panel(
                wf.TUNE_SCENE, wf.MATERIAL_PANEL,
                group=wf.MATERIAL_GROUP)
            infos = self._validate_tuning_materials(infos)
            self._material_cache = infos
            self._food_count_overrides = {}  # 清空覆盖，使用 OCR 原始值
            # 日志：记录缓存的材料数量（同名材料去重，保留最高置信度）
            deduped = self._dedup_by_confidence(infos)
            counts = {label: self._get_count(info)
                      for label, info in deduped.items()}
            logger.debug(f"材料缓存已刷新: {counts}")
        else:
            self._material_cache = None
            self._food_count_overrides = {}

    def invalidate_cache(self):
        """退出调律页时清空缓存"""
        self._material_cache = None

    def _get_count(self, info) -> int | None:
        """获取材料数量：优先查覆盖字典，否则用 info.count（只读属性）"""
        food_type = getattr(info, "type", "")
        if food_type in self._food_count_overrides:
            return self._food_count_overrides[food_type]
        return info.count

    def _dedup_by_confidence(self, infos: dict | None) -> dict[str, object]:
        """同名材料去重：保留置信度最高的槽

        材料区可能将同一材料误识别到多个槽（如第6列真槽 + 第7列幽灵槽），
        返回 {type: info} 字典，同名时保留 confidence 最高者；
        置信度相同时，优先保留 count 有效的槽（防止幽灵槽覆盖真槽）。
        """
        result: dict[str, object] = {}
        for info in (infos or {}).values():
            label = getattr(info, "type", "")
            if not label:
                continue
            existing = result.get(label)
            if existing is None:
                result[label] = info
            else:
                old_conf = getattr(existing, "confidence", 0) or 0
                new_conf = getattr(info, "confidence", 0) or 0
                # 置信度更高 → 替换
                # 置信度相同 → 保留 count 有效的（防止幽灵槽覆盖真槽）
                if new_conf > old_conf:
                    result[label] = info
                elif new_conf == old_conf:
                    old_count = getattr(existing, "count", None)
                    new_count = getattr(info, "count", None)
                    if old_count is None and new_count is not None:
                        result[label] = info
        return result

    def decrement_food(self, food: str):
        """调律后扣减本轮使用的狗粮数量（缓存内 -1）

        Args:
            food: 本轮使用的狗粮类型名（如「彩狗粮」「金狗粮」），空串不操作
        """
        if not food or not self._material_cache:
            return
        for info in self._material_cache.values():
            if getattr(info, "type", "") == food and info.count is not None:
                current = self._food_count_overrides.get(food, info.count)
                self._food_count_overrides[food] = max(0, current - 1)
                logger.debug(f"狗粮扣减: {food} → {self._food_count_overrides[food]}")
                return

    def get_material_stock(self) -> dict[str, int]:
        """当前材料库存快照（供进度对话框显示）

        返回 {材料名: 数量} 字典，同名材料去重后取当前有效数量。
        无缓存时返回空字典。
        """
        if not self._material_cache:
            return {}
        deduped = self._dedup_by_confidence(self._material_cache)
        result: dict[str, int] = {}
        for label, info in deduped.items():
            count = self._get_count(info)
            if count is not None:
                result[label] = count
        return result

    def tune_once(self, equip_data: EquipmentData,
                  expect_rating: str | None,
                  full_recycle_mode: bool = False) -> dict | None:
        """执行一轮调律：展开材料区→逐轮狗粮决策→一键添加→调律→收结果。

        石头检查与狗粮决策共用同一次材料区识别；决策结果存入
        round_food/round_food_reason 供调用方写说明文档。
        返回调律结果 OCR dict（由调用方挂进本件 report 的 tune_results）；
        返回 None 表示应终止循环（无添加入口/材料不足/规则判跳过装备，
        原因文案存入 abort_reason 供说明文档）。
        添加过狗粮时，关闭结果弹窗后还会补扫一次狗粮返还弹窗并补关。
        """
        wf = self._wf
        self.abort_reason = ""
        self.round_food = ""
        self.round_food_reason = ""
        self.round_food_refunded = False  # 重置返还标记
        add_scan = wf.ocr_scene(wf.TUNE_SCENE, ["auto_add", "auto_add_2"])
        can_add = tr("添加") in add_scan.get("auto_add", "")
        if tr("添加") in add_scan.get("auto_add_2", ""):
            wf.click_region(wf.TUNE_SCENE, "expand")
            wf.wait_stable("page_refresh")  # 展开添加面板
            can_add = True
        if not can_add:
            logger.info("未找到「添加」入口，视为无法继续调律")
            self.abort_reason = "未找到「添加」入口"
            return None

        # 使用缓存的材料数据：大律准石检查 + 逐轮狗粮决策共用
        # 缓存由 cache_materials() 在进入调律页/重置后刷新，
        # 每轮调律后由 decrement_food() 扣减狗粮数量
        settings = self._wf.base_group.materials
        infos = self._material_cache
        if not self._check_stone_stock(settings, infos):
            return None

        # 调满后回收模式：跳过狗粮决策
        food = ""
        if full_recycle_mode:
            self.round_food = ""
            self.round_food_reason = tr("调满后回收模式，跳过狗粮添加")
            logger.info(f"狗粮策略: {self.round_food_reason}")
        else:
            decision = self._decide_food_round(
                equip_data, settings, infos, expect_rating)
            self.round_food_reason = decision.reason
            if decision.action == "skip":
                self.abort_reason = decision.reason
                return None
            food = decision.food if decision.action == "feed" else ""
            self.round_food = food
        if food:
            # 同名幽灵槽防护：只认数量有效的槽位
            slot = next(
                ((r, c) for (r, c), i in (infos or {}).items()
                 if getattr(i, "type", "") == food
                 and getattr(i, "count", None) is not None), None)
            if not slot:
                logger.warning(f"{food} 材料槽位定位失败，提前结束调律")
                self.abort_reason = f"{food} 材料槽位定位失败"
                return None
            row, col = slot
            wf.click_panel(wf.TUNE_SCENE, wf.MATERIAL_PANEL, row, col)
            wf.wait_delay("step_interval")

        wf.click_region(wf.TUNE_SCENE, "auto_add")
        wf.wait_delay("step_interval")
        # 添加后确认按钮真的变成了「调律」，未就绪走材料不足处理
        if not self._ensure_tune_ready(settings):
            return None
        wf.click_region(wf.TUNE_SCENE, "tune_btn")
        wf.wait_delay("step_interval")
        wf.wait_stable("page_refresh")  # 调律结果出现

        result = wf.ocr_scene(wf.RESULT_SCENE, ["tune_affix", "tune_tip"])
        logger.info(f"调律结果: {result}")
        wf.click_region(wf.RESULT_SCENE, "close_btn")
        wf.wait_delay("step_interval")

        # 狗粮返还机制：添加过狗粮的轮次，关闭结果弹窗后可能命中概率返还，
        # 再弹一个无边框弹窗（同样 tune_tip「点击空白区域关闭」）。若不补关，
        # 它会挡住「一键添加」等后续点击，导致误判无法继续调律。
        if food:
            wf.wait_stable("page_refresh")  # 关闭结果弹窗后检查狗粮返还
            bonus = wf.ocr_scene(wf.RESULT_SCENE, ["tune_tip"])
            if bonus.get("tune_tip"):
                logger.info(f"检测到狗粮返还弹窗: {bonus}，补点一次关闭")
                wf.click_region(wf.RESULT_SCENE, "close_btn")
                wf.wait_delay("step_interval")
                self.round_food_refunded = True  # 标记返还，调用方据此恢复缓存
        return result

    def _validate_tuning_materials(self, infos: dict | None) -> dict | None:
        """调律流程材料校验：投入必须为 0（仅记录 error，不阻断）

        调律时材料通过点击/一键添加投入，识别时投入数量必然为 0。
        若识别到投入 > 0，记录 error 供后续分析，但数据照常传递。
        """
        if not infos:
            return infos
        for slot_key, info in infos.items():
            if not getattr(info, "type", ""):
                continue
            devoted = getattr(info, "devoted", None)
            if devoted is not None and devoted != 0:
                logger.error(
                    f"材料校验异常: {slot_key} devoted={devoted} != 0，"
                    f"数量识别可能有误（仅记录，不阻断）")
        return infos

    def _decide_food_round(self, equip_data: EquipmentData,
                           settings: MaterialSettings,
                           infos: dict | None,
                           expect_rating: str | None) -> FoodDecision:
        """逐轮狗粮决策：按材料设置规则表（首词条/期望/品阶三条件）
        与本轮材料区持有量决策（与石头检查共用同一次识别）"""
        if not settings.food_rules:
            return FoodDecision("none", "", tr("未配置狗粮规则 → 不添加"))
        cap_pct = (equip_data.affixes[0].cap_pct
                   if equip_data.affixes else None)
        # 同名材料去重：保留最高置信度的槽
        deduped = self._dedup_by_confidence(infos)
        stocks: dict[str, int | None] = {}
        for label, info in deduped.items():
            # 数量 None = 该材料是装备而非狗粮，视为已耗尽（count=0）
            count = self._get_count(info) if self._get_count(info) is not None else 0
            stocks[label] = count
        decision = settings.decide_food(
            int(cap_pct) if cap_pct is not None else None, expect_rating, equip_data.quality, stocks)
        log = logger.warning if decision.action == "skip" else logger.info
        log(f"狗粮策略: {decision.reason}")
        return decision

    def _check_stone_stock(self, settings: MaterialSettings,
                           infos: dict | None) -> bool:
        """大律准石数量检查（材料设置可开关，默认关闭）

        基于调律页材料区识别结果（infos，与逐轮狗粮决策共用同一次
        识别），取大律准石持有量（count 字段）；
        低于基准判材料不足，按配置的不足处理执行：skip=跳过该装备
        （继续遍历）；ask=confirm 弹窗询问，确认继续则本次运行不再
        检查，拒绝同 abort；abort=置 materials_exhausted 使 is_stopped
        恒真，全部退出。材料区找不到大律准石视为已耗尽；数量 OCR
        失败时警告放行（识别波动不误杀整次运行）。

        Returns:
            True=可继续调律；False=材料不足，本件终止（是否全退
            由 materials_exhausted 决定）
        """
        if not settings.stone_check_enabled or self._stone_check_waived:
            return True
        # 同名材料去重：保留最高置信度的槽
        deduped = self._dedup_by_confidence(infos)
        stone = deduped.get(STONE_LABEL)
        if stone is None:
            stock = 0  # 材料区无大律准石，视为已耗尽
        else:
            raw_count = self._get_count(stone)
            if raw_count is None:
                # 数量识别失败 = 该材料是装备而非调律石，视为已耗尽
                logger.warning("大律准石数量识别失败（实为装备），视为材料不足")
                stock = 0
            else:
                stock = raw_count
        if stock >= settings.stone_min_count:
            logger.debug(
                f"大律准石库存 {stock} >= 基准 {settings.stone_min_count}")
            return True
        reason = (f"大律准石 {stock} < 基准 "
                  f"{settings.stone_min_count}，材料不足")
        action = settings.stone_insufficient_action
        if action == "ask":
            choice = self._confirm_material_insufficient(
                f"{reason}，请选择：")
            if choice == "continue":
                logger.warning(f"{reason}，用户确认继续，本次运行不再检查")
                self._stone_check_waived = True
                return True
            elif choice == "skip":
                logger.warning(f"{reason}，用户选择跳过该装备")
                self.abort_reason = f"{reason}，跳过该装备"
                self._on_materials_insufficient(stock, settings.stone_min_count)
                return False
            else:  # end
                logger.warning(f"{reason}，用户选择结束本次调律")
                self.abort_reason = reason
                self.materials_exhausted = True
                self._wf.output["stop_reason"] = reason
                self._on_materials_insufficient(stock, settings.stone_min_count)
                return False
        if action == "skip":
            logger.warning(f"{reason}，跳过该装备")
            self.abort_reason = f"{reason}，跳过该装备"
            self._on_materials_insufficient(stock, settings.stone_min_count)
            return False
        # abort / ask 拒绝 → 全部退出
        logger.warning(f"{reason}，终止全部调律")
        self.abort_reason = reason
        self.materials_exhausted = True
        self._wf.output["stop_reason"] = reason
        self._on_materials_insufficient(stock, settings.stone_min_count)
        return False

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
            materials_exhausted 决定），原因已存 abort_reason
        """
        wf = self._wf
        btn = ""
        for attempt in range(2):
            if attempt:
                wf.wait_stable("page_refresh")  # 重试时等按钮就绪
            btn = wf.ocr_scene(wf.TUNE_SCENE, ["tune_btn"]).get(
                "tune_btn", "") or ""
            if tr("调律") in btn:
                return True
        reason = (f"一键添加后「调律」按钮未就绪"
                  f"（OCR: {btn or tr('空')}），疑似材料不足")
        action = (settings.stone_insufficient_action
                  if settings.stone_check_enabled else "skip")
        if action == "ask":
            if self._tune_ready_waived:
                action = "skip"
            else:
                choice = self._confirm_material_insufficient(
                    f"{reason}，请选择：")
                if choice == "continue":
                    self._tune_ready_waived = True
                    action = "skip"
                elif choice == "skip":
                    action = "skip"
                else:  # end
                    action = "abort"
        if action == "skip":
            logger.warning(f"{reason}，结束本件调律")
            self.abort_reason = f"{reason}，结束本件调律"
            return False
        # abort / ask 拒绝 → 全部退出
        logger.warning(f"{reason}，终止全部调律")
        self.abort_reason = reason
        self.materials_exhausted = True
        wf.output["stop_reason"] = reason
        return False

    def _confirm_material_insufficient(self, message: str) -> str:
        """材料不足时三选项确认：continue/skip/end

        经 engine._ui_callback 调度到 Qt 主线程弹三按钮对话框，
        无回调时回退为拒绝（按 end 处理）。
        同时经 _progress_hub 向进度对话框推送状态消息。
        """
        wf = self._wf
        # 向进度对话框推送状态
        try:
            hub = getattr(wf.engine, '_progress_hub', None) if wf.engine else None
            if hub is not None:
                hub.status_message.emit(message)
        except Exception:
            pass
        if wf.engine is None or wf.engine._ui_callback is None:
            logger.warning("无 UI 回调，材料不足对话框无法弹出，按结束处理")
            return "end"
        result = wf.engine._ui_callback(
            "confirm3", message=message)
        return result if result in ("continue", "skip", "end") else "end"

    def _on_materials_insufficient(self, stock: int, baseline: int):
        """大律准石低于基准的后处理挂载点（预留：补货/兑换）。
        当前仅记录不动作；不足处理（跳过/结束/询问）由
        _check_stone_stock 按配置执行。"""
        logger.info(f"  [材料不足] 大律准石 {stock}/基准 {baseline}"
                    f"（补货/兑换后处理待实现，仅记录）")
