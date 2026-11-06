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
        tr("编辑器打开之后，磁盘上的这些配置被其他窗口或程序改过：")
        + "\n\n" + files + "\n\n"
        + tr("选择「是」会用当前编辑内容覆盖它们，磁盘上的改动将丢失；"
             "选择「否」放弃本次保存，请关闭编辑器重新打开、确认最新内容后再改。"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
