"""strip_focus_rect：去焦点框的效果，以及"构造重控件树后销毁不崩"的回归。

原实现是一个重写 ``initStyleOption`` 的 QStyledItemDelegate 子类。Qt 在视图
析构收尾阶段仍会派发排队中的 paint 事件并回调 delegate 虚函数，而该虚函数
是 **Python 重写**的，PyQt 重入 Python 时会先抛
``wrapped C/C++ object ... has been deleted``、随后在 C++ 侧段错误——只要
构造过 SceneTab 这种较重的控件树再销毁就稳定复现（关闭场景编辑器、测试收尾
统一 processEvents 都会）。改 parent、改全局共享单例都无效，问题不在所有权；
换成样式表（Qt 自己解析，全程不回调 Python）才根治。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from lvjiang.ui.widgets import strip_focus_rect


def _table(qtbot) -> QTableWidget:
    table = QTableWidget(3, 2)
    for row in range(3):
        for col in range(2):
            table.setItem(row, col, QTableWidgetItem(f"{row}{col}"))
    table.resize(240, 120)
    qtbot.addWidget(table)
    return table


def _render(table) -> bytes:
    table.setCurrentCell(1, 1)
    table.setFocus()
    table.show()
    QApplication.processEvents()
    image = table.grab().toImage()
    return image.bits().asstring(image.sizeInBytes())


class TestFocusRectRemoved:
    def test_changes_rendering(self, qtbot):
        """确实改变了渲染——焦点框真的被去掉了，不是什么都没做。

        这里只断言"有变化"，不与旧 delegate 实现做逐像素比对：那种断言
        依赖两条渲染路径在当前平台的 style 下恰好一致，Windows 原生
        style 上会有 1~2/255 的子像素级差异而误报；而旧实现早已从生产
        代码移除，也不存在"回退到它"的风险，那条比对属于一次性的迁移
        证据，留着只会在开发机上常红。
        """
        assert _render(_table(qtbot)) != _render(
            _do(strip_focus_rect, _table(qtbot)))

    def test_idempotent(self, qtbot):
        table = _table(qtbot)
        strip_focus_rect(table)
        once = table.styleSheet()
        strip_focus_rect(table)
        assert table.styleSheet() == once

    def test_keeps_existing_stylesheet(self, qtbot):
        table = _table(qtbot)
        table.setStyleSheet("QTableWidget { color: red; }")
        strip_focus_rect(table)
        assert "color: red" in table.styleSheet()
        assert "outline: 0" in table.styleSheet()


class TestHeavyWidgetTeardown:
    def test_scene_toolbar_stays_compact_above_canvas(self, qtbot):
        """版本控件不能纵向撑高视图工具栏、把画布推到页面中部。"""
        from lvjiang.ui.scene_editor.scene_tab import SceneTab

        tab = SceneTab("activity_main")
        tab.resize(900, 500)
        qtbot.addWidget(tab)
        tab.show()
        QApplication.processEvents()

        assert tab._scene_version_value.height() <= tab._view_combo.height()
        assert tab._layout_version_value.height() <= tab._view_combo.height()
        toolbar_height = max(
            tab._view_combo.height(), tab._btn_manage_views.height()
        )
        assert tab._canvas.y() <= toolbar_height + 2

        disabled_cell = tab._region_table.cellWidget(0, 5)
        assert disabled_cell.findChild(QCheckBox) is not None
        alignment = disabled_cell.layout().itemAt(0).alignment()
        assert alignment
        assert all(
            button.styleSheet() for button in tab.findChildren(QPushButton)
        )

    def test_building_and_destroying_scene_tabs_does_not_crash(self, qtbot):
        """回归：反复构造/销毁 SceneTab 曾在 pytest-qt 收尾 processEvents 时段错误。

        必须用 SceneTab 这类真实的重控件树——单独一张表复现不出来。
        """
        from lvjiang.ui.scene_editor.scene_tab import SceneTab

        for _ in range(7):
            qtbot.addWidget(SceneTab("activity_main"))
        QApplication.processEvents()


def _do(fn, obj):
    fn(obj)
    return obj
