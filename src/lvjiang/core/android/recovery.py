"""每个设备共用的连接恢复编排；连接探测不等于动作已成功。"""
from __future__ import annotations

import subprocess
import time
from enum import Enum
from typing import TYPE_CHECKING, Callable

from loguru import logger

if TYPE_CHECKING:
    from .device import AdbDevice


class AdbConnectionError(RuntimeError):
    """恢复被停止，或没有人工恢复入口。"""


class ChannelFaultError(AdbConnectionError):
    """执行通道故障（视频流/ADB/代理）且已放弃自动恢复。

    与任务内业务异常必须分开：批量遇到它要中止整批，而不是记一条失败继续
    下一项——通道坏了，后续每一项都只是拿旧帧空转。
    """


class RecoveryAction(Enum):
    RETRY = "retry"
    FALLBACK = "fallback"


class AndroidRecovery:
    def __init__(self, device: AdbDevice):
        self.device = device
        # recovering：传输正在（或刚刚）重建，成功一次命令后复位——它不表示
        # 「有人正阻塞在人工确认上」，透明重连也算在内。
        self.recovering = False
        # waiting：此刻确实有命令阻塞在 wait_for_user 里；waiting_kind 只在该
        # 等待期间有意义（宿主据此判断重连能不能代替用户点「继续」）。
        self.waiting = False
        self.waiting_kind = "connection"

    def check_stop(self) -> None:
        if self.device.stop_check and self.device.stop_check():
            raise AdbConnectionError("连接恢复已停止")

    def wait_interval(self) -> None:
        deadline = time.monotonic() + self.device.recovery.retry_interval
        while True:
            self.check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def retry(self, channel: str, once: Callable[[], bool], attempts: int,
              errors: tuple[type[Exception], ...]) -> bool:
        for attempt in range(1, attempts + 1):
            self.check_stop()
            logger.info("[{}] 检查并恢复连接 {}/{}", channel, attempt, attempts)
            try:
                if once():
                    return True
            except errors as error:
                logger.debug("[{}] 本次恢复未完成：{}", channel, error)
            if attempt < attempts:
                self.wait_interval()
        return False

    def wait_for_user(self, error: Exception, description: str, *, uncertain: bool = False,
                      kind: str | None = None) -> None:
        self.recovering = True
        self.waiting_kind = kind or ("uncertain_action" if uncertain else "connection")
        message = f"{description} 失败: {error}"
        logger.warning("执行通道等待人工确认：{}", message)
        if self.device.on_connection_lost is None:
            raise AdbConnectionError(f"{description} 失败且无恢复通道: {error}") from error
        # waiting 从「把问题交给用户」那一刻起为真：宿主在这段期间到达的重连
        # 必须能看出有人在等确认。
        self.waiting = True
        try:
            self.device.resume_event.clear()
            self._notify(self.device.on_connection_lost, "断线", message)
            if self.device.can_wait_for_recovery is not None and not self.device.can_wait_for_recovery():
                raise AdbConnectionError(f"{description} 失败；当前没有运行任务，请重新连接设备后重试") from error
            while True:
                if self.device.stop_check and self.device.stop_check():
                    self.device.resume_event.set()
                    raise AdbConnectionError(f"{description} 失败且用户停止: {error}") from error
                if self.device.resume_event.wait(timeout=0.1):
                    self.device.resume_event.clear()
                    self.check_stop()
                    logger.info("用户确认继续，重新检查执行通道")
                    return
        finally:
            # 等待结束（确认、停止或抛错）就不再有人等：kind 必须一起复位，
            # 否则宿主会把「上一次等了什么」误当成「现在在等什么」。
            self.waiting = False
            self.waiting_kind = "connection"

    def recover_adb(self, error: Exception, description: str, *, retry_safe: bool,
                    failed_attempts: int = 1) -> bool:
        self.recovering = True
        uncertain = isinstance(error, subprocess.TimeoutExpired) and not retry_safe
        confirmed = False
        while True:
            ready = self.device.reconnect()
            if uncertain:
                self.wait_for_user(error, description + "；动作可能已经执行，确认继续将重新尝试", uncertain=True)
                uncertain = False
                confirmed = True
                if not ready:
                    continue
            elif retry_safe and ready and failed_attempts >= self.device.recovery.adb_attempts:
                # transport 探测正常也不代表截图/查询能成功，不能无限自动重放读取。
                self.wait_for_user(error, description + "；读取重试耗尽，请检查设备后重新尝试")
                confirmed = True
                failed_attempts = 0
            if ready:
                return confirmed
            self.wait_for_user(error, description + "；请恢复 ADB 连接")
            confirmed = True
            failed_attempts = 0

    def recover_stream(self, stream: object) -> None:
        """建立/恢复推送式视频流：自动重试耗尽后无限等待人工。

        每轮 = ``stream_attempts`` 次重建会话；一轮失败就交给人工（点「重新检查」
        开始新一轮，计数随之重置，不会因为"已经试过 N 次"拒绝再试）。用户选择
        停止时抛 :class:`ChannelFaultError`，上层据此按「通道故障」中止整批，
        而不是记一条业务失败继续下一项。

        这里不做超时：通道停下后任务本身已经无法推进，自动放弃只会把一次可恢复
        的故障变成一次失败。
        """
        attempts = max(1, int(getattr(self.device.recovery, "stream_attempts", 5)))
        self.recovering = True
        try:
            while True:
                for attempt in range(1, attempts + 1):
                    self.check_stop()
                    logger.info("[流式] 重建视频流 {}/{}", attempt, attempts)
                    restart = getattr(stream, "restart", None)
                    if callable(restart) and restart():
                        clear = getattr(stream, "clear_fault", None)
                        if callable(clear):
                            clear()
                        logger.info("[流式] 视频流已恢复")
                        self.succeeded()
                        return
                    logger.warning(
                        "[流式] 重建失败 {}/{}：{}", attempt, attempts,
                        getattr(stream, "last_fault_detail", "") or "未知原因")
                    if attempt < attempts:
                        self.wait_interval()
                error = ChannelFaultError(
                    "视频流恢复失败："
                    + (getattr(stream, "last_fault_detail", "") or "未知原因"))
                try:
                    self.wait_for_user(error, "视频流", kind="stream")
                except AdbConnectionError as stopped:
                    # 用户选择停止，或没有可用的恢复入口（CLI）：按通道故障上报
                    raise ChannelFaultError(str(stopped)) from stopped
        finally:
            self.recovering = False

    def after_agent_failure(self, error: Exception, description: str, *,
                            uncertain: bool, fallback_supported: bool) -> RecoveryAction:
        self.recovering = True
        ready = self.device.reconnect()
        if uncertain:
            self.wait_for_user(error, description + "已发送但未取得响应，动作可能已经执行；确认继续将重新尝试当前动作",
                               uncertain=True)
        elif ready and fallback_supported and self.device.recovery.allow_adb_fallback:
            return RecoveryAction.FALLBACK
        else:
            detail = "请恢复代理或 ADB 连接" if fallback_supported else "当前操作需要设备端代理，请恢复手机代理"
            self.wait_for_user(error, f"{description}；{detail}")
        return RecoveryAction.RETRY

    def succeeded(self) -> None:
        if self.recovering:
            self.recovering = False
            self._notify(self.device.on_connection_restored, "已恢复")

    def _notify(self, callback: Callable[..., None] | None, what: str, *args) -> None:
        """调用 UI 恢复通知，失败只记日志。

        这些回调跨线程指向宿主控件（Qt 信号桥），目标被重连替换后控件可能
        已经被销毁。通知失败不能改变命令结果：调用方正在重试原命令，异常
        传播出去会被上层记成任务失败，一次正常恢复就变成整条任务中断。
        """
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # noqa: BLE001 - 通知边界必须吞掉一切异常
            logger.warning("[恢复] {}通知失败（已忽略）: {}", what, exc)
