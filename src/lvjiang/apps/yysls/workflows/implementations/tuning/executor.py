"""调律执行 — TuningExecutor

单轮调律操作：材料识别、狗粮决策、石头检查、就绪确认。
通过 wf 引用访问 UI 操作原语（click_region / ocr_scene 等）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    STONE_LABEL,
    FoodDecision,
    MaterialSettings,
    get_tuning_base,
)

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
    """

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf
        self.abort_reason = ""
        self.round_food = ""
        self.round_food_reason = ""
        self.materials_exhausted = False
        self._stone_check_waived = False
        self._tune_ready_waived = False

    def reset_state(self):
        """每次 run 开始时重置运行期状态"""
        self.materials_exhausted = False
        self._stone_check_waived = False
        self._tune_ready_waived = False

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
        add_scan = wf.ocr_scene(wf.TUNE_SCENE, ["auto_add", "auto_add_2"])
        can_add = "添加" in add_scan.get("auto_add", "")
        if "添加" in add_scan.get("auto_add_2", ""):
            wf.click_region(wf.TUNE_SCENE, "expand")
            wf.wait_delay("page_refresh_wait")
            can_add = True
        if not can_add:
            logger.info("未找到「添加」入口，视为无法继续调律")
            self.abort_reason = "未找到「添加」入口"
            return None

        # 一次材料区识别：大律准石检查 + 逐轮狗粮决策共用
        settings = get_tuning_base().materials
        infos = None
        if settings.stone_check_enabled or settings.food_rules:
            infos = wf.recognize_materials_info_panel(
                wf.TUNE_SCENE, wf.MATERIAL_PANEL,
                group=wf.MATERIAL_GROUP)
            # 调律流程特有校验：投入必须为 0（材料通过点击/一键添加投入）
            infos = self._validate_tuning_materials(infos)
        if not self._check_stone_stock(settings, infos):
            return None

        # 调满后回收模式：跳过狗粮决策
        food = ""
        if full_recycle_mode:
            self.round_food = ""
            self.round_food_reason = "调满后回收模式，跳过狗粮添加"
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
        wf.wait_delay("page_refresh_wait")  # 调律结果出现（after_tune_wait 已废弃）

        result = wf.ocr_scene(wf.RESULT_SCENE, ["tune_affix", "tune_tip"])
        logger.info(f"调律结果: {result}")
        wf.click_region(wf.RESULT_SCENE, "close_btn")
        wf.wait_delay("step_interval")

        # 狗粮返还机制：添加过狗粮的轮次，关闭结果弹窗后可能命中概率返还，
        # 再弹一个无边框弹窗（同样 tune_tip「点击空白区域关闭」）。若不补关，
        # 它会挡住「一键添加」等后续点击，导致误判无法继续调律。
        if food:
            wf.wait_delay("page_refresh_wait")
            bonus = wf.ocr_scene(wf.RESULT_SCENE, ["tune_tip"])
            if bonus.get("tune_tip"):
                logger.info(f"检测到狗粮返还弹窗: {bonus}，补点一次关闭")
                wf.click_region(wf.RESULT_SCENE, "close_btn")
                wf.wait_delay("step_interval")
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
            stocks[label] = info.count
        decision = settings.decide_food(
            cap_pct, expect_rating, equip_data.quality, stocks)
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
        stone = next((i for i in (infos or {}).values()
                      if getattr(i, "type", "") == STONE_LABEL), None)
        if stone is None:
            stock = 0  # 材料区无大律准石，视为已耗尽
        else:
            stock = stone.count
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
                wf.wait_delay("page_refresh_wait")
            btn = wf.ocr_scene(wf.TUNE_SCENE, ["tune_btn"]).get(
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
            self.abort_reason = f"{reason}，结束本件调律"
            return False
        # abort / ask 拒绝 → 全部退出
        logger.warning(f"{reason}，终止全部调律")
        self.abort_reason = reason
        self.materials_exhausted = True
        wf.output["stop_reason"] = reason
        return False

    def _confirm_continue(self, message: str) -> bool:
        """走 DSL confirm 内置函数弹窗询问用户

        有 engine 引用时经 engine._ui_callback 调度到 Qt 主线程，
        无则回退平台原生弹窗（confirm 内置函数自行处理）。
        """
        wf = self._wf
        return bool(wf.call_function("confirm", [message],
                                     engine=wf.engine))

    def _on_materials_insufficient(self, stock: int, baseline: int):
        """大律准石低于基准的后处理挂载点（预留：补货/兑换）。
        当前仅记录不动作；不足处理（跳过/结束/询问）由
        _check_stone_stock 按配置执行。"""
        logger.info(f"  [材料不足] 大律准石 {stock}/基准 {baseline}"
                    f"（补货/兑换后处理待实现，仅记录）")
