"""Profile 周期服务的桌面线程与信号适配。"""
from __future__ import annotations

import time

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.profile.engine import ProfileProcessor
from ...core.profile.events import observe_updates
from ...core.users.document import UserConfigManager

#: 接近边界时改用细粒度睡眠，让总览在 05:00:00 之后的一个片内就重画。
_FINE_SLEEP_MS = 100
_COARSE_SLEEP_MS = 1000
_COARSE_UNTIL_SECONDS = 2.0


class ProfileEngine(QThread):
    TICK_INTERVAL = 60
    alert_triggered = pyqtSignal(str, str, str)
    data_updated = pyqtSignal(str)

    def __init__(self, user_manager: UserConfigManager, parent=None):
        super().__init__(parent)
        self._engine_running = True
        self._unobserve_updates = observe_updates(self.data_updated.emit)
        self._processor = ProfileProcessor(
            user_manager, should_stop=lambda: not self._engine_running,
            notify_alert=self.alert_triggered.emit, notify_updated=self.data_updated.emit)

    def request_stop(self) -> None:
        """请求引擎停止（线程安全）"""
        self._engine_running = False
        self._unobserve_updates()

    def _sleep_until_next_tick(self) -> None:
        """睡到下一个 tick：最迟 60 秒，但要在最近的周期边界之后立刻醒来。

        quota 的归零是读时派生的，界面靠一次重画才会「当场变 0」。若按固定
        60 秒轮询，05:00 归零最多会晚 59 秒才上屏——所以这里把唤醒时刻对齐到
        边界，并在最后两秒切成 100ms 粒度。
        """
        delay = self._processor.seconds_to_next_boundary()
        wait = float(self.TICK_INTERVAL)
        if delay is not None:
            # 留 0.1 秒余量：边界那一秒已经过去，派生值才切换到新周期。
            wait = min(wait, max(0.1, delay + 0.1))
        deadline = time.monotonic() + wait
        while self._engine_running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.msleep(
                _FINE_SLEEP_MS if remaining <= _COARSE_UNTIL_SECONDS
                else _COARSE_SLEEP_MS
            )

    def run(self):
        """主循环：tick 一次、睡到下一个边界或 60 秒，直到引擎被 request_stop"""
        logger.info("ProfileEngine 已启动")

        while self._engine_running:
            try:
                self._processor.tick()
            except Exception as e:
                logger.error(f"ProfileEngine tick 异常: {e}")

            self._sleep_until_next_tick()

        logger.info("ProfileEngine 已停止")

        logger.info("ProfileEngine 已停止")

# ─── 引擎单例管理 ────────────────────────────────────────────

_engine: ProfileEngine | None = None


def get_or_create_engine(
    user_manager: UserConfigManager,
) -> ProfileEngine:
    """获取或创建 ProfileEngine 单例"""
    global _engine
    if _engine is None:
        _engine = ProfileEngine(user_manager)
    return _engine


def stop_engine() -> None:
    """停止并销毁 ProfileEngine"""
    global _engine
    if _engine is not None:
        logger.info("正在停止 ProfileEngine...")
        _engine.request_stop()
        _engine.quit()
        if not _engine.wait(5000):
            logger.warning("ProfileEngine 5 秒内未退出，强制终止")
            _engine.terminate()
            _engine.wait(2000)
        _engine = None
