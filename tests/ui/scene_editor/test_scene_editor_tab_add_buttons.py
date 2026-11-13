"""分组/场景条右端的 "+" 必须落到所在分组，空分组也不能失去入口。"""

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QMenu, QPushButton

from lvjiang.core.scene import registry as scene_registry
from tests.ui.scene_editor.test_scene_editor_reload import editor as editor


def test_scene_tab_add_button_follows_its_group(editor, qtbot):
    dialog, _system, _manager = editor
    registry = scene_registry.get_registry()
    registry.create_group("spare", "备用")
    registry.save_group_config()
    scene_registry.reload_scene_registry()
    dialog._rebuild_group_tabs()
    dialog.show()
    qtbot.wait(10)

    group_widget = dialog._group_tab_widget
    group_bar = group_widget.tabBar()
    group_btn = group_widget.add_button
    assert isinstance(group_btn, QPushButton)
    assert group_btn.text() == "+"
    # 不参与焦点链，避免点过一次后按空格/回车误触发新建。
    assert group_btn.focusPolicy() == Qt.FocusPolicy.NoFocus
    # "+" 要贴着最后一个分组，而不是吸附在整条最右侧。
    last_group = group_bar.tabRect(group_bar.count() - 1)
    last_right = group_bar.mapTo(group_widget, last_group.topRight()).x()
    assert group_btn.x() - last_right <= 8

    spare_tabs = dialog._group_tabs["spare"]
    spare_btn = spare_tabs.add_button
    assert isinstance(spare_btn, QPushButton)
    # 空分组没有页签时 Tab 条会塌成 0 高，按钮必须仍有点高度。
    assert spare_tabs.tabBar().height() > 0
    assert spare_btn.height() > 0
    # 没有 Tab 时 "+" 贴在条的最左端。
    assert spare_btn.x() <= 6

    created: list[str] = []
    dialog._on_new_scene = created.append  # type: ignore[method-assign]
    dialog._group_tab_widget.setCurrentWidget(spare_tabs)
    spare_btn.click()
    assert created == ["spare"]


def test_tab_context_menus_create_in_the_clicked_strip(editor, qtbot, monkeypatch):
    dialog, _system, _manager = editor
    dialog.show()
    qtbot.wait(10)

    calls: list[str] = []
    monkeypatch.setattr(dialog, "_on_new_group", lambda: calls.append("group"))
    monkeypatch.setattr(dialog, "_on_new_scene", calls.append)

    def choose(label: str):
        def fake_exec(menu, *args, **kwargs):
            return next(a for a in menu.actions() if a.text() == label)
        return fake_exec

    monkeypatch.setattr(QMenu, "exec", choose("新建分组"))
    dialog._on_group_tab_context_menu(QPoint(2, 2))
    assert calls == ["group"]

    monkeypatch.setattr(QMenu, "exec", choose("新建场景"))
    dialog._on_scene_tab_context_menu(QPoint(2, 2), "general")
    assert calls == ["group", "general"]


def test_tab_context_menu_dismiss_does_nothing(editor, monkeypatch):
    """菜单被取消时 exec() 返回 None，不能误当成第一个动作执行。"""
    dialog, _system, _manager = editor
    calls: list[str] = []
    monkeypatch.setattr(dialog, "_on_new_group", lambda: calls.append("new_group"))
    monkeypatch.setattr(
        dialog, "_do_rename_group", lambda gk: calls.append("rename_group"))
    monkeypatch.setattr(
        dialog, "_do_delete_group", lambda gk: calls.append("delete_group"))
    monkeypatch.setattr(
        dialog, "_on_new_scene", lambda gk: calls.append("new_scene"))
    monkeypatch.setattr(
        dialog, "_do_rename_scene", lambda sk: calls.append("rename_scene"))
    monkeypatch.setattr(
        dialog, "_do_delete_scene", lambda sk: calls.append("delete_scene"))
    monkeypatch.setattr(QMenu, "exec", lambda menu, *a, **k: None)

    dialog._on_group_tab_context_menu(QPoint(2, 2))
    dialog._on_scene_tab_context_menu(QPoint(2, 2), "general")
    assert calls == []


def test_tab_context_menu_ignores_canvas_area(editor, qtbot, monkeypatch):
    """右键画布/页面区域不能冒出分组/场景的新建菜单。"""
    dialog, _system, _manager = editor
    dialog.show()
    qtbot.wait(10)

    opened: list[QMenu] = []
    monkeypatch.setattr(QMenu, "exec", lambda menu, *a, **k: opened.append(menu))

    page = dialog._current_scene_tab()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, QPoint(60, 80))
    QApplication.sendEvent(page, event)
    assert opened == []
