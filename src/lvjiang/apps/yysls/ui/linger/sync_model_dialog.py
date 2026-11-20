"""同步模型配置：勾选候选 profile key、设默认同步 key。

顶部原来的「同步模型」下拉直接列全部库存 key（30+ 个），噪音大且没有
配额。这个对话框把它们收进来管：

- 候选：勾选的 key 进条目编辑器与顶部快捷切换，全量列表在对话框里搜；
- 默认同步 key：固定/逐位条目「跟随同步模型」时的入账目标；
- 「不同步」也可作为候选与默认（只记录、不写任何 profile key）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWidgets import (
    QVBoxLayout as QVBox,
)

from .....i18n import tr
from .....ui.widgets.button_styles import apply_button_style
from ...core.linger import booking
from ...core.linger import overrides as ov
from ...core.linger.models import NO_SYNC_TARGET
from .item_dialog import target_choices

_STATUS_NEUTRAL = "palette(mid)"


class LingerSyncModelDialog(QDialog):
    """维护入账目标候选与默认同步 key。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("同步模型"))
        self.setModal(True)
        self.setMinimumSize(520, 560)
        self._build_ui()

    # ── 构建 ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        hint = QLabel(tr(
            "勾选的 key 进入条目编辑与顶部切换的候选；「默认同步 key」是"
            "固定/逐位条目（未自选目标时）的入账目标。"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_STATUS_NEUTRAL}; font-size: 11px;")
        outer.addWidget(hint)

        # 默认同步 key 行
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel(tr("默认同步 key")))
        self.default_edit = QLineEdit(self)
        self.default_edit.setReadOnly(True)
        default_row.addWidget(self.default_edit, 1)
        outer.addLayout(default_row)

        # 搜索框
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(tr("搜索 key…"))
        self.search_edit.textChanged.connect(self._apply_filter)
        outer.addWidget(self.search_edit)

        # 候选列表（可滚动）：库存 + 配额 + 不同步
        self._all_entries: list[tuple[str, str, str]] = [
            (value, label, tr("库存"))
            for value, label in target_choices(booking.MODEL_STOCK)
        ] + [
            (value, label, tr("配额"))
            for value, label in target_choices(booking.MODEL_QUOTA)
        ]
        chosen = set(ov.read_target_candidates())
        self._checkboxes: dict[str, QCheckBox] = {}

        content = QWidget()
        column = QVBox(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        for value, label, model_label in self._all_entries:
            row = QCheckBox(f"{label}　[{model_label}]", self)
            row.setProperty("key_value", value)
            row.setChecked(value in chosen)
            row.stateChanged.connect(lambda _state, v=value: self._on_toggle(v))
            self._checkboxes[value] = row
            column.addWidget(row)
        nosync = QCheckBox(f"{tr('不同步')}　[{tr('记录')}]  ", self)
        nosync.setProperty("key_value", NO_SYNC_TARGET)
        nosync.setChecked(NO_SYNC_TARGET in chosen)
        nosync.stateChanged.connect(lambda _state: self._on_toggle(NO_SYNC_TARGET))
        self._checkboxes[NO_SYNC_TARGET] = nosync
        column.addWidget(nosync)
        column.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        # 按钮行
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.use_default_button = QPushButton(tr("设为默认"))
        apply_button_style(self.use_default_button, variant="neutral")
        self.use_default_button.setEnabled(False)
        self.use_default_button.clicked.connect(self._set_default_from_selection)
        buttons.addWidget(self.use_default_button)
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

        self._refresh_default()

    # ── 行为 ──────────────────────────────────────────────

    def _current_candidates(self) -> list[str]:
        return [value for value, box in self._checkboxes.items() if box.isChecked()]

    def _on_toggle(self, value: str) -> None:
        # 候选即时保存（勾选即生效，关闭不丢）；记住最后操作行供「设为默认」
        self._last_toggled = value
        ov.set_target_candidates(self._current_candidates(),
                                 ov.read_default_target())
        self._refresh_default()

    def _set_default_from_selection(self) -> None:
        value = getattr(self, "_last_toggled", "")
        if not value:
            return
        ov.set_target_candidates(self._current_candidates(), value)
        self._refresh_default()

    def _refresh_default(self) -> None:
        current = ov.read_default_target()
        self.default_edit.setText(
            tr("不同步") if current == NO_SYNC_TARGET else current)
        self.use_default_button.setEnabled(
            bool(getattr(self, "_last_toggled", ""))
            and self._last_toggled != current)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for value, box in self._checkboxes.items():
            box.setVisible(not needle or needle in value.lower()
                           or needle in box.text().lower())
            if not needle and value == NO_SYNC_TARGET:
                box.setVisible(True)
        # 「设为默认」需要先有焦点候选：简化为勾选态变化时刷新
        self.use_default_button.setEnabled(False)
