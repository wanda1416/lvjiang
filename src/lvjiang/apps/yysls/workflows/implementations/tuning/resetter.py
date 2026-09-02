"""调律重置用例。

负责次数、冷却和材料门槛检查，以及重置确认交互；不处理装备回收。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from lvjiang.apps.yysls.workflows.implementations.tuning.ports import ResetHostPort
from lvjiang.workflows.builtins.system import pause_user

from ......i18n import tr
from ....tuning_history.models import (
    RESET_COOLDOWN,
    RESET_COUNT_UNREADABLE,
    RESET_FAILED,
    RESET_MATERIAL_SHORTAGE,
)

# reset_check 的两种游戏原文：
#   可重置：当前装备剩余可重置次数:1
#   冷却期：6小时32分后可调律重置
# 匹配的是游戏截屏 OCR 出来的文字，游戏本身只有中文，不随本软件界面语言
# 变化，绝不能过 tr()（英文界面下会拿翻译后的英文去匹配中文截屏）。
#
# 判据取「次数」而不是「可重置」：后者虽然不是冷却文案的子串，却是它的
# **子序列**（可…调律…重置），OCR 少认「调律」两字就成了 "…后可重置"，
# 冷却中的装备会被误判成可重置，接着去点根本不存在的确认。「次数」两字
# 与冷却文案没有任何字符交集，漏字漏成它是不可能的；而漏认导致的反向
# 误判只会让本可重置的装备被当成冷却期跳过，是安全的那一侧。
_RESET_AVAILABLE_TOKEN = "次数"

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.tuning.route_strategy import (
        TuningRouteStrategy,
    )


class TuningResetter:
    """执行一次受规则约束的调律重置。"""

    def __init__(self, wf: ResetHostPort, routes: TuningRouteStrategy):
        self._wf = wf
        self._routes = routes

    def reset_remaining(self) -> int | None:
        """读取重置按钮并解析剩余次数；``None`` 表示没读出明确数字。

        必须区分这两件事：读到 "重置调律(0)" 是**无疑义的次数用尽**，可以按
        配置转回收；而按钮整个没识别上（空串、把 0 读成 o、弹窗还没渲染完）
        是识别异常，下一次可能就读到了，绝不能当成 0 去触发回收。
        """
        wf = self._wf
        raw = wf.ocr_scene(wf.TUNE_SCENE, ["reset_tune"]).get(
            "reset_tune", ""
        ) or ""
        match = re.search(r"(\d+)\s*[/／]\s*\d+", raw)
        if match:
            return int(match.group(1))
        match = re.search(r"\d+", raw)
        return int(match.group()) if match else None

    def try_reset_tune(
        self,
        cfg: Any,
        resets_used: int,
        why: str,
        min_material_count: int | None = None,
    ) -> bool | tuple[str, str]:
        """检查约束并提交重置。

        - ``True``：重置成功。
        - ``False``：**确定且永久**的重置不可用，调用方按
          ``reset_exhausted_action`` 处置（可能是回收）。只有两种情况够格：
          按钮上读到明确的 0，或本地 ``max_resets`` 门槛。
        - ``(outcome, message)``：本次没做成，强制跳过该装备。``outcome`` 是
          ``tuning_history.models`` 里的 ``RESET_*`` 码，调用方直接透传，
          不再靠中文子串反推——那在英文界面下必然认错。
        """
        wf = self._wf
        logger.debug(
            "  [try_reset_tune] min_material_count={}, resets_used={}",
            min_material_count,
            resets_used,
        )
        if resets_used >= 1:
            logger.info("  本件已重置过一次，冷却期内不再重置")
            return (RESET_COOLDOWN, tr("本件已重置过一次，冷却期内不再重置"))
        if resets_used >= cfg.max_resets:
            logger.info("  重置次数已达上限（{}），不再重置", cfg.max_resets)
            return False
        remaining = self.reset_remaining()
        if remaining is None:
            logger.warning("  未能识别重置调律按钮上的剩余次数，跳过该装备")
            return (RESET_COUNT_UNREADABLE,
                    tr("无法识别重置次数，跳过该装备"))
        if remaining == 0:
            logger.info("  重置调律按钮无剩余次数，不再重置")
            return False

        logger.info("  执行重置调律（剩余次数 OCR: {}）：{}", remaining, why)
        wf._emit_operation(
            "reset",
            f"准备重置，剩余次数 {remaining}，正在检查冷却状态",
            reason=why,
            resets=resets_used,
        )
        self._routes.open_reset_dialog()
        wf.wait_stable("page_refresh")
        check_text = wf.ocr_scene(wf.TUNE_SCENE, ["reset_check"]).get(
            "reset_check", ""
        ) or ""
        if _RESET_AVAILABLE_TOKEN not in check_text:
            logger.info(
                "  冷却期检查未通过（reset_check={!r}），装备在冷却期，降级跳过",
                check_text,
            )
            self._close_dialog()
            return (RESET_COOLDOWN, tr("装备重置冷却期，跳过该装备"))

        if min_material_count is not None:
            wf._emit_operation(
                "reset",
                "冷却检查通过，正在读取重置材料数量",
                reason=why,
                resets=resets_used,
            )
            reset_info = wf.ocr_scene(wf.TUNE_SCENE, ["reset_info"]).get(
                "reset_info", ""
            ) or ""
            material_count = self.parse_material_count(reset_info)
            if material_count is None:
                logger.error(
                    "  reset_info 未识别到「持有」关键字（reset_info={!r}），跳过",
                    reset_info,
                )
                self._close_dialog()
                return (RESET_FAILED, tr("重置材料信息识别失败，跳过该装备"))
            if material_count < min_material_count:
                logger.info(
                    "  重置材料不足（持有 {} < 要求 {}），跳过该装备",
                    material_count,
                    min_material_count,
                )
                self._close_dialog()
                return (RESET_MATERIAL_SHORTAGE, tr(
                    "传律石不够（持有 {held} < 要求 {need}），跳过该装备"
                ).format(held=material_count, need=min_material_count))
            logger.info(
                "  重置材料检查通过（持有 {} >= 要求 {}）",
                material_count,
                min_material_count,
            )

        wf._emit_operation(
            "reset", "检查通过，正在执行两次重置确认", reason=why, resets=resets_used
        )
        first_confirm = wf.ocr_scene(
            wf.TUNE_SCENE, ["reset_confirm"]).get("reset_confirm", "") or ""
        if not first_confirm:
            logger.warning("  未识别到首次重置确认按钮，取消重置")
            self._close_dialog()
            return (RESET_FAILED, tr("重置确认按钮识别失败，跳过该装备"))
        self._routes.confirm_reset("reset_confirm")
        wf.wait_delay("secondary_confirm")
        second_confirm = wf.ocr_scene(
            wf.CONTROL_SCENE, ["confirm"]).get("confirm", "") or ""
        if not second_confirm:
            # 首次确认点下去之后必然弹二次确认。弹不出来通常不是识别问题，
            # 而是账号开了安全锁需要解锁，机器自己点不出来。此时既不能替用户
            # 猜着取消（取消要先退模态、再退确认重置视图，中途状态不可控），
            # 也不能当没事继续，只能停下来交给人。
            logger.error("  未识别到二次重置确认按钮，暂停等待人工介入")
            wf._emit_operation(
                "reset", "重置二次确认未出现，已暂停等待人工处理",
                reason=why, resets=resets_used)
            pause_user(
                wf.engine,
                "重置：点击确认重置后没有出现二次确认弹窗。"
                "常见原因是账号开启了安全锁，需要先手动解锁。"
                "请处理好现场后点击确定，程序会重新检查一次。",
            )
            second_confirm = wf.ocr_scene(
                wf.CONTROL_SCENE, ["confirm"]).get("confirm", "") or ""
            if not second_confirm:
                logger.warning("  人工介入后仍未识别到二次确认，跳过该装备")
                self._close_dialog()
                return (RESET_FAILED, tr("重置二次确认识别失败，跳过该装备"))
        self._routes.confirm_reset("confirm")
        wf._emit_operation("reset", "重置已提交，正在读取重置结果")
        wf.wait_stable("page_refresh")
        # 重置提交后：105 级直接回到调律页面；110 级会返还材料，多一层提示要
        # 关掉。这里不按等级分支，统一点一次 close_btn：它本质是一块安全的空
        # 关闭区（桌面布局绑定为 SPACE），有提示就关掉，没提示落在调律页面上
        # 也无副作用。
        #
        # 不用 general_control.blank_area：自动调律这几个弹窗上可能有按钮与它
        # 叠加，点下去会误触。blank_area 在调律流程里只作识别区，不作点击目标。
        # 若将来游戏改了这里的行为，从这条日志定位。
        logger.debug("  重置完成，点击 close_btn 关闭可能存在的材料返还提示")
        wf.click_region(wf.TUNE_SCENE, "close_btn")
        wf.wait_stable("page_refresh")
        return True

    @staticmethod
    def parse_material_count(text: str) -> int | None:
        """解析「持有 N」；缺少“持有”返回 None，缺少数字返回 0。

        text 是游戏截屏 OCR 结果，恒为中文，不能过 tr()（同上）。
        """
        if "持有" not in text:
            return None
        match = re.search(r"持有\s*(\d+)", text)
        return int(match.group(1)) if match else 0

    def _close_dialog(self) -> None:
        # 用 reset_tune 视图自己的 reset_back，不用基底视图的 back：两者都在
        # 右上角，但位置有细微差别，拿基底坐标关重置弹窗是压着"恰好重合"在赌。
        self._wf.click_region(self._wf.TUNE_SCENE, "reset_back")
        self._wf.wait_delay("step_interval")
