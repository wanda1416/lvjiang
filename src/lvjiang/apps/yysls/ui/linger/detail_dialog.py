"""只读详情窗口：双击清单行时看这一条的完整说明。

列表里只放摘要，长说明（怎么接、在哪、注意什么）放这里整段显示，支持选中复制。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr, tr_label
from .....ui.widgets.button_styles import apply_button_style
from ...core.linger.models import LingerItem, LingerRegion


class LingerDetailDialog(QDialog):
    """一条来源的只读详情。"""

    def __init__(self, region: LingerRegion, item: LingerItem,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{item.display_name} · {region.label}")
        self.setModal(False)
        self.setMinimumSize(560, 420)
        self._item = item

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        head = QLabel(f"{item.display_name}　·　{region.label}")
        outer.addWidget(head)
        meta = QLabel(tr_label("来源") + item.source)
        meta.setStyleSheet("color: palette(mid); font-size: 11px;")
        outer.addWidget(meta)

        body = QPlainTextEdit(self)
        body.setReadOnly(True)
        body.setPlainText(item.detail.strip() or tr("暂无详情"))
        body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        outer.addWidget(body, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = QPushButton(tr("关闭"))
        apply_button_style(close_button, variant="neutral")
        close_button.clicked.connect(self.close)
        bottom.addWidget(close_button)
        outer.addLayout(bottom)
