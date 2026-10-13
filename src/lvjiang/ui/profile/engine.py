"""Profile 周期服务的桌面线程与信号适配。"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.profile.engine import ProfileProcessor
from ...core.profile.events import observe_updates
from ...core.user_config import UserConfigManager


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

    def run(self):
        """主循环：每 60 秒 tick 一次，直到引擎被 request_stop"""
        logger.info("ProfileEngine 已启动")

        while self._engine_running:
            try:
                self._processor.tick()
            except Exception as e:
                logger.error(f"ProfileEngine tick 异常: {e}")

            # 分段睡眠，便于快速响应停止
            for _ in range(self.TICK_INTERVAL):
                if not self._engine_running:
                    break
                self.msleep(1000)

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
