"""游戏配置对话框

独立窗口，管理装备配置、词条配置与流派配置。
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout

from .....i18n import tr
from .....ui.button_styles import apply_dialog_button_box_style
from ...config import get_game_config
from .config_tab import GameConfigTab


class GameConfigDialog(QDialog):
    """游戏配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("游戏配置"))
        self.setMinimumSize(900, 700)
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)

        # manager 是进程级单例；每次打开都重读一次，以便发现
        # 两次对话框之间的外部改动。YAML 未变时只做 stat + 缓存拷贝。
        manager = get_game_config()
        manager.reload()
        self._data = manager.get_raw()
        self._dirty = False
        self._tab = GameConfigTab(
            data=self._data, on_changed=self._mark_dirty)
        self._layout.addWidget(self._tab)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Discard)
        self._save_button = self._buttons.button(
            QDialogButtonBox.StandardButton.Save)
        self._save_button.setText(tr("保存"))
        self._save_button.setEnabled(False)
        self._discard_button = self._buttons.button(
            QDialogButtonBox.StandardButton.Discard)
        self._discard_button.setText(tr("撤销"))
        self._discard_button.setEnabled(False)
        self._buttons.accepted.connect(self._save)
        self._discard_button.clicked.connect(self._discard_changes)
        apply_dialog_button_box_style(self._buttons)
        self._layout.addWidget(self._buttons)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._save_button.setEnabled(True)
        self._discard_button.setEnabled(True)

    def _save(self) -> None:
        try:
            get_game_config().save(self._data)
            self._tab.save_auxiliary_config()
        except Exception as exc:  # noqa: BLE001 - 保存失败必须留在对话框中
            QMessageBox.warning(self, tr("保存失败"), str(exc))
            return
        self._dirty = False
        self._save_button.setEnabled(False)
        self._discard_button.setEnabled(False)

    def _discard_changes(self) -> None:
        if not self._dirty:
            return
        answer = QMessageBox.question(
            self,
            tr("撤销游戏配置"),
            tr("确定撤销本次所有未保存的游戏配置更改吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        manager = get_game_config()
        manager.reload()
        self._data = manager.get_raw()
        old_tab = self._tab
        self._tab = GameConfigTab(
            data=self._data, on_changed=self._mark_dirty)
        self._layout.replaceWidget(old_tab, self._tab)
        old_tab.deleteLater()
        self._dirty = False
        self._save_button.setEnabled(False)
        self._discard_button.setEnabled(False)

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            tr("未保存的更改"),
            tr("游戏配置有未保存的更改，确定退出并放弃这些更改吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def reject(self) -> None:
        if self._confirm_discard():
            super().reject()

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            # 先清脏再走 QDialog 默认关闭链，确保 rejected/finished 正常发出，
            # 同时避免默认 closeEvent -> reject 时重复询问。
            self._dirty = False
            super().closeEvent(event)
        else:
            event.ignore()

    def select_school_base_attr(self, school: str, base_attr: str) -> None:
        """显示后定位到流派配置及指定基础属性。"""
        QTimer.singleShot(
            0, lambda: self._tab.select_school_base_attr(school, base_attr),
        )
