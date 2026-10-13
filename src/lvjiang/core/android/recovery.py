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


class RecoveryAction(Enum):
    RETRY = "retry"
    FALLBACK = "fallback"


class AndroidRecovery:
    def __init__(self, device: AdbDevice):
        self.device = device
        self.recovering = False
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

    def wait_for_user(self, error: Exception, description: str, *, uncertain: bool = False) -> None:
        self.recovering = True
        self.waiting_kind = "uncertain_action" if uncertain else "connection"
        message = f"{description} 失败: {error}"
        logger.warning("执行通道等待人工确认：{}", message)
        if self.device.on_connection_lost is None:
            raise AdbConnectionError(f"{description} 失败且无恢复通道: {error}") from error
        self.device.resume_event.clear()
        self.device.on_connection_lost(message)
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
            if self.device.on_connection_restored:
                self.device.on_connection_restored()
