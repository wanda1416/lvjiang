from PyQt6.QtCore import QRect
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
)

from lvjiang.ui.combo_box import (
    AutoWidthComboBox,
    ComboWidthMode,
    combo_contents_width,
)


def test_dynamic_candidates_refresh_closed_and_popup_width(qtbot) -> None:
    combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
    qtbot.addWidget(combo)
    combo.addItem("短")
    before = combo.minimumWidth()

    combo.addItem("动态加入的很长候选名称")

    needed = combo_contents_width(combo)
    assert combo.minimumWidth() >= needed > before
    assert combo.view().minimumWidth() >= needed


def test_stretch_caps_closed_width_but_keeps_popup_complete(qtbot) -> None:
    combo = AutoWidthComboBox(content_width_cap=120)
    qtbot.addWidget(combo)
    combo.addItem("这是一个不应该无限撑宽主窗口的超长用户名或文件路径")

    needed = combo_contents_width(combo)
    assert combo.minimumWidth() == 120
    assert combo.view().minimumWidth() >= needed > combo.minimumWidth()


def test_default_mode_uses_content_as_minimum_without_consuming_surplus(qtbot) -> None:
    container = QWidget()
    qtbot.addWidget(container)
    layout = QHBoxLayout(container)
    combo = AutoWidthComboBox(minimum_width=100)
    combo.addItem("短")
    layout.addWidget(combo)
    layout.addStretch()
    container.resize(700, 80)
    container.show()
    qtbot.waitExposed(container)

    assert combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
    assert combo.width() < container.width() // 2

    combo.addItem("内容增长后下拉框才按照实际需要变宽")
    qtbot.waitUntil(lambda: combo.minimumWidth() > 100)
    assert combo.width() >= combo.minimumWidth()


def test_replacing_model_keeps_dynamic_sizing(qtbot) -> None:
    combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
    qtbot.addWidget(combo)
    model = QStandardItemModel(combo)
    combo.setModel(model)

    model.appendRow(QStandardItem("替换模型后的长候选"))

    assert combo.minimumWidth() >= combo_contents_width(combo)


def test_full_mode_gives_the_actual_edit_field_enough_text_width(qtbot) -> None:
    combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
    qtbot.addWidget(combo)
    text = "当前外功穿透（非定音部分）"
    combo.addItem(text)
    combo.resize(combo.minimumWidth(), combo.sizeHint().height())

    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.rect = QRect(0, 0, combo.width(), combo.height())
    style = combo.style()
    assert style is not None
    edit_width = style.subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    ).width()
    assert edit_width >= combo.fontMetrics().horizontalAdvance(text)
    assert combo.view().textElideMode().name == "ElideNone"
