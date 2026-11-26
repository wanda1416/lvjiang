"""导出装备数据的格式选择对话框。

只负责“导给哪个网站、导出后要不要顺手打开它”，真正的内容生成与落盘在
``bingjian_export`` / ``leoq7_export`` 与调用方。站点列表、网址、文件名后缀与
操作提示都来自 ``equipment_exchange.SITES``；界面文案只用带占位符的通用键渲染，
新增站点不需要新增翻译键。
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from lvjiang.ui.widgets.button_styles import apply_dialog_button_box_style

from .....i18n import tr
from .equipment_exchange import SITE_LEOQ7, SITES, site_spec

#: 选项标签模板；``{site}`` 填品牌名（品牌名不翻译）
SITE_OPTION_LABEL_TEMPLATE = "导出为 {site} 格式"


@dataclass(frozen=True)
class ExportChoice:
    """用户选择的导出目标。"""

    site: str
    open_site: bool


class ExportDialog(QDialog):
    """选择导出格式，并决定导出后是否打开对应网站。"""

    def __init__(self, parent=None, *, preferred_site: str = SITE_LEOQ7):
        super().__init__(parent)
        self.setWindowTitle(tr("导出装备数据"))
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        label_template = tr(SITE_OPTION_LABEL_TEMPLATE)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        for index, spec in enumerate(SITES.values()):
            button = QRadioButton(
                label_template.format(site=spec.label), self,
            )
            button.setChecked(spec.key == preferred_site)
            self._group.addButton(button, index)
            self._buttons[spec.key] = button
            layout.addWidget(button)
            hint = QLabel(tr(spec.hint_key).format(url=spec.host), self)
            hint.setWordWrap(True)
            hint.setIndent(22)
            layout.addWidget(hint)

        self._open_site = QCheckBox(tr("导出后用浏览器打开对应网站"), self)
        self._open_site.setChecked(True)
        layout.addSpacing(4)
        layout.addWidget(self._open_site)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText(tr("导出"))
        if cancel_button is not None:
            cancel_button.setText(tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_dialog_button_box_style(buttons)
        layout.addWidget(buttons)

        self.setMinimumWidth(430)

    def selected_site(self) -> str:
        for site, button in self._buttons.items():
            if button.isChecked():
                return site
        return SITE_LEOQ7

    def choice(self) -> ExportChoice:
        return ExportChoice(site=self.selected_site(), open_site=self._open_site.isChecked())

    def site_url(self, site: str | None = None) -> str:
        return site_spec(site or self.selected_site()).url
