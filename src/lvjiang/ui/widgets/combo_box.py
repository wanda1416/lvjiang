"""统一的下拉框内容宽度策略。

普通 ``QComboBox`` 的尺寸提示可以被布局压缩，固定像素又无法覆盖中文、
翻译和运行时动态候选。本模块让候选模型成为宽度的唯一来源，并在模型变化、
字体或主题变化后自动刷新。
"""

from __future__ import annotations

from enum import StrEnum

from PyQt6.QtCore import QAbstractItemModel, QEvent, QRect, QSize, Qt
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


class ComboWidthMode(StrEnum):
    """闭合下拉框的宽度策略；弹出列表始终按完整内容计算。"""

    FULL = "full"
    STRETCH = "stretch"
    POPUP = "popup"


def combo_contents_width(combo: QComboBox, *, minimum: int = 0) -> int:
    """返回能完整展示最长候选的样式化宽度。"""
    combo.ensurePolished()
    text_width = max(
        (combo.fontMetrics().horizontalAdvance(combo.itemText(index))
         for index in range(combo.count())),
        default=0,
    )
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    style = combo.style()
    assert style is not None
    width = style.sizeFromContents(
        QStyle.ContentsType.CT_ComboBox,
        option,
        QSize(text_width, combo.fontMetrics().height()),
        combo,
    ).width()
    option.rect = QRect(
        0, 0, width, max(combo.sizeHint().height(), combo.fontMetrics().height()),
    )
    edit_width = style.subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    ).width()
    # style.sizeFromContents 在部分 Windows/高 DPI 样式下仍会给 edit field
    # 留少几个像素，随后 QStyle 强制把中间文字画成“..”。按真实编辑区
    # 反补差额，不能凭外层控件看起来够宽就判断文字一定放得下。
    width += max(0, text_width - edit_width)
    return max(minimum, width + 4)


def fit_combo_popup_to_contents(combo: QComboBox, *, minimum: int = 0) -> int:
    """保持弹出列表完整，允许闭合框服从外部布局。"""
    width = combo_contents_width(combo, minimum=minimum)
    view = combo.view()
    assert view is not None
    view.setMinimumWidth(width)
    view.setTextElideMode(Qt.TextElideMode.ElideNone)
    return width


def fit_combo_to_contents(combo: QComboBox, *, minimum: int = 0) -> int:
    """保持闭合框与弹出列表均足以展示最长候选。"""
    width = fit_combo_popup_to_contents(combo, minimum=minimum)
    combo.setMinimumWidth(width)
    return width


class AutoWidthComboBox(QComboBox):
    """候选变化后自动刷新宽度的项目标准下拉框。

    ``STRETCH`` 是默认策略：闭合框从调用方给定的最小宽度起步，随内容
    增长到 ``content_width_cap``，但不会主动吞掉布局剩余空间；弹出列表
    始终完整。有限枚举可选 ``FULL``；用户名、路径等明确不应撑宽页面的
    场景可选 ``POPUP``。需要占满一行时由调用方显式设置布局 stretch。
    """

    def __init__(
        self,
        parent=None,
        *,
        width_mode: ComboWidthMode | str = ComboWidthMode.STRETCH,
        minimum_width: int = 0,
        content_width_cap: int = 360,
    ) -> None:
        super().__init__(parent)
        self._width_mode = ComboWidthMode(width_mode)
        self._width_floor = max(0, int(minimum_width))
        self._content_width_cap = max(self._width_floor, int(content_width_cap))
        self._applying_content_width = False
        self._sizing_model: QAbstractItemModel | None = None
        model = self.model()
        if model is not None:
            self._bind_sizing_model(model)
        self.currentIndexChanged.connect(self._refresh_content_width)
        self._refresh_content_width()

    def set_width_mode(
        self,
        mode: ComboWidthMode | str,
        *,
        content_width_cap: int | None = None,
    ) -> None:
        self._width_mode = ComboWidthMode(mode)
        if content_width_cap is not None:
            self._content_width_cap = max(
                self._width_floor, int(content_width_cap))
        self._refresh_content_width()

    def setMinimumWidth(self, width: int) -> None:  # noqa: N802 - Qt API
        if not getattr(self, "_applying_content_width", False):
            self._width_floor = max(0, int(width))
            if hasattr(self, "_content_width_cap"):
                self._content_width_cap = max(
                    self._content_width_cap, self._width_floor)
        super().setMinimumWidth(width)

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802
        old_model = getattr(self, "_sizing_model", None)
        if old_model is not None:
            self._unbind_sizing_model(old_model)
        super().setModel(model)
        if hasattr(self, "_sizing_model"):
            current_model = self.model()
            if current_model is not None:
                self._bind_sizing_model(current_model)
            self._refresh_content_width()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._refresh_content_width()
        super().showEvent(event)

    def showPopup(self) -> None:  # noqa: N802 - Qt API
        self._refresh_content_width()
        super().showPopup()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.LanguageChange,
        ):
            self._refresh_content_width()

    def _bind_sizing_model(self, model: QAbstractItemModel) -> None:
        self._sizing_model = model
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.modelReset,
            model.dataChanged,
            model.layoutChanged,
        ):
            signal.connect(self._refresh_content_width)

    def _unbind_sizing_model(self, model: QAbstractItemModel) -> None:
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.modelReset,
            model.dataChanged,
            model.layoutChanged,
        ):
            try:
                signal.disconnect(self._refresh_content_width)
            except (TypeError, RuntimeError):
                pass
        self._sizing_model = None

    def _refresh_content_width(self, *_args) -> None:
        if not hasattr(self, "_width_mode"):
            return
        needed = fit_combo_popup_to_contents(
            self, minimum=self._width_floor)
        if self._width_mode == ComboWidthMode.POPUP:
            target = self._width_floor
        elif self._width_mode == ComboWidthMode.FULL:
            target = needed
        else:
            target = min(needed, self._content_width_cap)
        self._applying_content_width = True
        try:
            super().setMinimumWidth(target)
        finally:
            self._applying_content_width = False
