"""跨用户装备冷却管理对话框。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lvjiang.i18n import tr
from lvjiang.ui.button_styles import apply_dialog_button_box_style

from ....core.loadout import LoadoutRepository
from .cards import (
    _CompactEquipCard,
    _format_cooldown_remaining,
    _parse_cooldown_time,
    _show_equipment_properties,
)

_COLUMN_COUNT = 6


@dataclass(frozen=True)
class _CooldownEquipmentEntry:
    username: str
    equip: dict
    expires_at: datetime


def _load_cooldown_entries(
    usernames: list[str],
    users_dir: Path | None = None,
) -> list[_CooldownEquipmentEntry]:
    """读取并按冷却到期时间升序汇总所有用户装备。"""
    entries: list[_CooldownEquipmentEntry] = []
    for username in usernames:
        try:
            repo = LoadoutRepository(username, users_dir)
            if not repo.path.exists():
                continue
            for equip in repo.load().equipment_items.values():
                expires_at = _parse_cooldown_time(
                    equip.get("cooldown_expires_at"))
                if expires_at is not None:
                    entries.append(_CooldownEquipmentEntry(
                        username, equip, expires_at))
        except Exception:
            logger.exception(f"读取用户 {username} 的冷却装备失败")
    return sorted(entries, key=lambda entry: entry.expires_at)


class _CooldownEquipmentTile(QWidget):
    """用户名、倒计时和共用装备卡片组成的单个展示项。"""

    def __init__(
        self,
        entry: _CooldownEquipmentEntry,
        display_params: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.entry = entry
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        username = QLabel(
            f"{tr('用户名')}：{entry.username}")
        username.setStyleSheet("font-weight:600;")
        layout.addWidget(username)

        remaining = _format_cooldown_remaining(
            entry.equip.get("cooldown_expires_at"))
        countdown = QLabel(f"{tr('冷却倒计时')}：{remaining}")
        countdown.setStyleSheet("color:#D97706;font-weight:600;")
        layout.addWidget(countdown)

        self.card = _CompactEquipCard(
            display_params, context_mode="properties")
        self.card.setMinimumWidth(190)
        self.card.set_equip(
            entry.equip, str(entry.equip.get("type") or tr("未知")))
        layout.addWidget(self.card)


class CooldownEquipmentDialog(QDialog):
    """以六列网格管理所有用户仍带冷却时间的装备。"""

    def __init__(
        self,
        usernames: list[str],
        display_params: dict,
        parent=None,
        *,
        users_dir: Path | None = None,
    ):
        super().__init__(parent)
        self._usernames = list(usernames)
        self._display_params = display_params
        self._users_dir = users_dir
        self.changed = False
        self._tiles: list[_CooldownEquipmentTile] = []

        self.setWindowTitle(tr("冷却装备"))
        self.resize(1500, 800)
        self.setMinimumSize(1000, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply_dialog_button_box_style(buttons)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(tr("关闭"))
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._rebuild()

    def _rebuild(self) -> None:
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)
        self._tiles = []
        entries = _load_cooldown_entries(self._usernames, self._users_dir)
        for index, entry in enumerate(entries):
            tile = _CooldownEquipmentTile(entry, self._display_params)
            tile.card.properties_requested.connect(
                partial(self._show_properties, entry))
            self._tiles.append(tile)
            grid.addWidget(tile, index // _COLUMN_COUNT, index % _COLUMN_COUNT)
        for column in range(_COLUMN_COUNT):
            grid.setColumnStretch(column, 1)
        if not entries:
            empty = QLabel(tr("当前没有设置冷却时间的装备"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                "color:palette(mid);font-size:14px;padding:48px;")
            grid.addWidget(empty, 0, 0, 1, _COLUMN_COUNT)
        grid.setRowStretch((len(entries) + _COLUMN_COUNT - 1) // _COLUMN_COUNT, 1)
        self._scroll.setWidget(content)

    def _show_properties(
        self,
        entry: _CooldownEquipmentEntry,
        _equip: dict,
    ) -> None:
        changed = False

        def update_cooldown(value: str) -> bool:
            nonlocal changed
            fp = str(entry.equip.get("_fp") or "")
            if not fp:
                QMessageBox.warning(
                    self, tr("修改失败"), tr("装备数据缺少 _fp 字段"))
                return False
            try:
                LoadoutRepository(
                    entry.username, self._users_dir,
                ).set_item_cooldown(fp, value)
            except Exception as exc:
                logger.exception(
                    f"修改用户 {entry.username} 的装备冷却时间失败")
                QMessageBox.critical(self, tr("修改失败"), str(exc))
                return False
            changed = True
            self.changed = True
            return True

        _show_equipment_properties(
            self, entry.equip, cooldown_changed=update_cooldown)
        if changed:
            self._rebuild()


__all__ = ["CooldownEquipmentDialog"]
