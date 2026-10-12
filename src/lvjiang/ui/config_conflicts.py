"""用户明确确认配置覆盖；工作线程不能弹阻塞窗口。"""
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QMessageBox

from ..i18n import tr


def confirm_config_overwrite(files: str) -> bool:
    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return False
    return QMessageBox.question(
        QApplication.activeWindow(), tr("磁盘配置已更新"),
        tr("编辑期间磁盘中的配置发生了变更。是否仍保存本次修改？") + "\n\n" + files,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
