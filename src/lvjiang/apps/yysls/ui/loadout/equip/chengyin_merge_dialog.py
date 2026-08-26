"""承音装备疑似同件快照的人工确认对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.core.loadout import ChengyinMergeCandidate
from lvjiang.i18n import tr
from lvjiang.ui.button_styles import apply_button_style

from .cards import _CompactEquipCard


class _CandidatePair(QFrame):
    def __init__(
        self,
        candidate: ChengyinMergeCandidate,
        display_params: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.candidate = candidate
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "_CandidatePair {border:1px solid palette(midlight);"
            "border-radius:6px;background:palette(window);}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._card_column(
            tr("旧版本"), candidate.old, display_params), 1)
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setStyleSheet("font-size:20px;color:palette(mid);")
        layout.addWidget(arrow)
        layout.addWidget(self._card_column(
            tr("保留版本"), candidate.new, display_params), 1)
        self.checkbox = QCheckBox(tr("合并"))
        self.checkbox.setToolTip(tr("删除左侧旧版本，并把备战方案引用迁移到右侧版本"))
        layout.addWidget(
            self.checkbox, alignment=Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _card_column(title: str, equip: dict, display_params: dict) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight:600;color:palette(mid);")
        layout.addWidget(label)
        card = _CompactEquipCard(display_params)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        card.setCursor(Qt.CursorShape.ArrowCursor)
        card.setMinimumWidth(190)
        card.set_equip(equip, str(equip.get("type") or tr("未知")))
        layout.addWidget(card)
        return wrapper


class ChengyinMergeDialog(QDialog):
    """两列展示全部候选，只有用户勾选并确认后才返回选择。"""

    def __init__(
        self,
        candidates: list[ChengyinMergeCandidate],
        display_params: dict,
        parent=None,
    ):
        super().__init__(parent)
        self._pairs: list[_CandidatePair] = []
        self.setWindowTitle(tr("承音装备合并"))
        self.resize(1500, 760)
        self.setMinimumSize(1000, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        summary = QLabel(tr(
            "目前识别出 {count} 组疑似同一件装备，请核对左右装备后选择是否合并。"
        ).format(count=len(candidates)))
        summary.setWordWrap(True)
        summary.setStyleSheet("font-size:14px;font-weight:600;")
        root.addWidget(summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for index, candidate in enumerate(candidates):
            pair = _CandidatePair(candidate, display_params)
            pair.checkbox.toggled.connect(self._update_merge_enabled)
            self._pairs.append(pair)
            grid.addWidget(pair, index // 2, index % 2)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        if not candidates:
            empty = QLabel(tr("没有找到符合条件的疑似重复装备"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                "color:palette(mid);font-size:14px;padding:48px;")
            grid.addWidget(empty, 0, 0, 1, 2)
        grid.setRowStretch((len(candidates) + 1) // 2, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.merge_button = QPushButton(tr("合并选中项"))
        self.merge_button.setEnabled(False)
        self.merge_button.clicked.connect(self.accept)
        cancel_button = QPushButton(tr("取消合并"))
        cancel_button.clicked.connect(self.reject)
        apply_button_style(self.merge_button, variant="action")
        apply_button_style(cancel_button, variant="neutral")
        footer.addWidget(self.merge_button)
        footer.addWidget(cancel_button)
        root.addLayout(footer)

    def _update_merge_enabled(self) -> None:
        self.merge_button.setEnabled(any(
            pair.checkbox.isChecked() for pair in self._pairs))

    def selected_candidates(self) -> list[ChengyinMergeCandidate]:
        return [
            pair.candidate for pair in self._pairs
            if pair.checkbox.isChecked()
        ]


__all__ = ["ChengyinMergeDialog"]
