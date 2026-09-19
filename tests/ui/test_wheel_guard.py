"""全局滚轮拦截：下拉框 / 数字输入框（含子类）不响应滚轮，滚动交给父级滚动区域。"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.widgets import install_wheel_guard


def _wheel(widget) -> bool:
    event = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, -120), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event.isAccepted()


def test_wheel_does_not_change_combo_or_spinbox_values(qtbot):
    install_wheel_guard(QApplication.instance())

    class _Combo(QComboBox):
        pass

    combo = _Combo()
    combo.addItems(["a", "b", "c"])
    spin = QSpinBox()
    spin.setRange(0, 10)
    dspin = QDoubleSpinBox()
    dspin.setRange(0, 10)
    area = QScrollArea()
    qtbot.addWidget(area)
    inner = QWidget()
    layout = QVBoxLayout(inner)
    for widget in (combo, spin, dspin):
        layout.addWidget(widget)
    for index in range(60):
        layout.addWidget(QLabel(f"row {index}"))
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.resize(200, 200)
    area.show()
    qtbot.waitExposed(area)

    for widget in (combo, spin, dspin):
        before = area.verticalScrollBar().value()
        _wheel(widget)
        assert area.verticalScrollBar().value() > before   # 页面照常滚动
    assert combo.currentIndex() == 0
    assert spin.value() == 0
    assert dspin.value() == 0
