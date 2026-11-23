"""无人值守：起步画面恢复与中止后的收尾。

无人值守模式下没人盯着屏幕，客户端一旦停在竖屏/登录前状态，后续每一轮都会
失败。这里负责两件事：

- 起步：批量从竖屏画面开始、或恢复 wf 已配好时，先借异常恢复 wf 把客户端带
  回登录主页（``_recover_to_login_at_start``、``_needs_portrait_recovery``）；
- 中止：无人值守被异常打断后，把游戏收回公共初始页（``_recover_unattended``）。

这些方法通过 ``self`` 使用编排器的引擎、用户表与消息回调。
"""

from __future__ import annotations

from typing import Any, cast

from loguru import logger

from ...workflows.result_protocol import RESULT_STOPPED, RESULT_SUCCESS
from ..layout.config import canvas_aspect_matches
from ..layout.models import Layout
from .models import BatchStageResult, UnattendedInterrupt


class UnattendedMixin:
    """无人值守相关的恢复与收尾。"""


    def _unattended_active(self) -> bool:
        """无人值守是否真的生效：必须同时配好恢复 wf。

        少了恢复 wf 就改道 pause 只会把整批推到错误页面上连环失败，
        不如照旧弹窗等人。配置层已经拦了一道，这里不依赖它。
        """
        return bool(self._spec.unattended
                    and self._spec.workflows.recover_unattended)


    def _unattended_ui_callback(self, kind: str, **kwargs) -> object:
        """用户决策交互按既有契约透传宿主，仅 pause 触发无人值守异常恢复。"""
        if kind != "pause":
            callback = self._ctx.ui_callback
            return callback(kind, **kwargs) if callback is not None else None
        text = str(kwargs.get("message") or kind)
        self._unattended_hit = text
        raise UnattendedInterrupt(text)


    def _needs_portrait_recovery(self) -> bool:
        """批量起步画面是竖屏、且交换宽高后才匹配布局时，先收回登录主页。

        与启动门禁共用 ``canvas_aspect_matches``：本来就按竖屏设计的布局
        在正常比较时就已匹配，不会被误判成临时竖屏而多跑一次恢复。
        """
        if not self._spec.allow_portrait:
            return False
        if self._ctx.target_kind != "adb":
            return False
        layout = cast(Layout | None, self._ctx.layout)
        capture: Any = self._ctx.capture
        if layout is None or not layout.aspect or capture is None:
            return False
        try:
            width, height = capture.get_capture_size()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"批量启动取截图尺寸失败，跳过竖屏恢复: {exc}")
            return False
        if width <= 0 or height <= width:
            return False
        passed, portrait_used = canvas_aspect_matches(
            layout, float(width), float(height), allow_portrait=True)
        return bool(passed and portrait_used)


    def _start_recovery_username(self) -> str:
        """挑一个能支撑客户端重启的用户：优先带账号和区服后缀的候选。"""
        if self._spec.execution_unit_key == "user":
            candidates = list(self._usernames)
        else:
            candidates = []
            for unit in self._usernames:
                candidates.extend(self._unit_members.get(unit, []))
        for name in candidates:
            attributes = self._user_attributes.get(name, {})
            if attributes.get("account") and attributes.get("tail"):
                return name
        return candidates[0] if candidates else ""


    def _recover_to_login_at_start(self, batch_state: dict) -> dict:
        """批量起步于竖屏时，先借异常恢复 wf 把客户端带到登录主页。

        只回登录主页（pending=False），不代登录当前角色：具体用户由随后的
        prepare_item 按本次批量的第一条目正常选择。
        """
        wf_name = self._spec.workflows.recover_unattended
        if not wf_name:
            return batch_state
        username = self._start_recovery_username()
        self._log("[批量] 启动画面为竖屏，执行异常恢复 wf 回到登录主页")
        result = self._run_stage(
            "recover_unattended", wf_name, -1, username, batch_state,
            extra_variables={
                "batch_recover_pending": False,
                "batch_recover_username": username,
            })
        if result.status == RESULT_STOPPED:
            self._stopped = True
            return batch_state
        if result.status != RESULT_SUCCESS:
            self._log(f"[批量] 启动时异常恢复未成功：{result.message}", "warning")
            return batch_state
        return result.state if result.state is not None else batch_state


    def _recover_unattended(self, run_idx: int, username: str,
                            batch_state: dict, *, pending: bool) -> BatchStageResult | None:
        """无人值守中止后把游戏收回公共初始页（登录页 - 登录主页视图）。

        所有批量任务都从这个视图起步，所以恢复目标只有这一个。具体怎么收——
        先看是不是已经在登录主页、启动页点返回、还是强制重启客户端——交给
        配置的恢复 wf：重启是框架能力，不该写进某个业务任务里。

        只在确实撞了弹窗时动作：任务因别的原因失败时画面通常还在可用状态，
        没有理由顺手重启一次客户端。

        ``pending`` 为真表示本条目还有未执行的任务，恢复 wf 据此决定要不要
        继续登录回游戏主页。
        """
        if not self._unattended_hit:
            return None
        text, self._unattended_hit = self._unattended_hit, ""
        wf_name = self._spec.workflows.recover_unattended
        if not wf_name:
            self._log(
                f"[批量] 无人值守中止（{text}）；未配置恢复 wf，直接继续下一个任务")
            return None
        self._log(f"[批量] 无人值守中止（{text}）；执行恢复 wf 收回登录主页")
        result = self._run_stage(
            "recover_unattended", wf_name, run_idx, username, batch_state,
            extra_variables={
                # 本条目还有没跑的任务时，恢复流程不止回登录主页，还要用当前
                # 角色重新登录到游戏主页，调度器才能接着跑下一项；全跑完了就
                # 停在登录主页等下一个用户。
                "batch_recover_pending": bool(pending),
                "batch_recover_username": username,
            })
        self._unattended_hit = ""
        if result.status == RESULT_STOPPED:
            self._stopped = True
        elif result.status != RESULT_SUCCESS:
            self._log(f"[批量] 无人值守恢复未成功：{result.message}", "warning")
        return result
