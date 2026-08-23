"""插件注入机制测试

覆盖三条注入路径（不实例化完整 MainWindow，沿用桩式风格）：
- register_hooks：多插件 Tab/菜单按注册顺序叠加、window_title 后注册者覆盖
- MainWindow._add_plugin_tabs：按序追加、builder 异常只记日志不中断
- MainWindow._setup_menu：菜单 builder 注入在「帮助」之前、异常不中断
- RunControlMixin._on_start：F9 按当前左侧 Tab 的 f9_run 鸭子类型分发
"""

from PyQt6.QtWidgets import QMainWindow

import lvjiang.ui.main_window as mw_module
from lvjiang.apps import register_hooks
from lvjiang.apps.base import AppHooks
from lvjiang.ui.main_window import MainWindow
from lvjiang.ui.run_control import RunControlMixin

# ─── register_hooks 多插件叠加 ─────────────────────────────

class TestRegisterHooksStacking:
    def test_tabs_and_menus_stack_in_order(self):
        registry: dict = {}
        b1, b2, b3, m1, m2 = (lambda h: None,) * 5
        register_hooks(AppHooks(
            name="A", window_title="标题A",
            left_tab_builders=[("A左", b1)],
            right_tab_builders=[("A右", b2)],
            menu_builders=[m1],
        ), registry)
        register_hooks(AppHooks(
            name="B", window_title="标题B",
            left_tab_builders=[("B左", b3)],
            menu_builders=[m2],
        ), registry)

        assert [label for label, _ in registry["left_tab_builders"]] == ["A左", "B左"]
        assert [label for label, _ in registry["right_tab_builders"]] == ["A右"]
        assert registry["menu_builders"] == [m1, m2]
        # window_title 后注册者覆盖
        assert registry["window_title"] == "标题B"


# ─── _add_plugin_tabs 消费注入 ─────────────────────────────

class _FakeTabs:
    """QTabWidget 桩：记录 addTab 调用"""

    def __init__(self):
        self.added: list[tuple[object, str]] = []

    def addTab(self, widget, label):
        self.added.append((widget, label))


class TestAddPluginTabs:
    def test_builders_called_in_order_with_host(self, monkeypatch):
        host = object()  # 方法不碰 self，仅把 host 透传给 builder
        seen_hosts = []

        def build_a(h):
            seen_hosts.append(h)
            return "widget_a"

        def build_b(h):
            seen_hosts.append(h)
            return "widget_b"

        monkeypatch.setattr(mw_module, "get_registry", lambda: {
            "left_tab_builders": [("甲", build_a), ("乙", build_b)],
        })
        tabs = _FakeTabs()
        MainWindow._add_plugin_tabs(host, tabs, "left_tab_builders")

        assert tabs.added == [("widget_a", "甲"), ("widget_b", "乙")]
        assert seen_hosts == [host, host]

    def test_builder_exception_does_not_interrupt(self, monkeypatch):
        host = object()

        def boom(h):
            raise RuntimeError("builder 炸了")

        monkeypatch.setattr(mw_module, "get_registry", lambda: {
            "right_tab_builders": [("好", lambda h: "w1"),
                                   ("坏", boom),
                                   ("也好", lambda h: "w2")],
        })
        tabs = _FakeTabs()
        MainWindow._add_plugin_tabs(host, tabs, "right_tab_builders")

        assert tabs.added == [("w1", "好"), ("w2", "也好")]

    def test_empty_registry_is_noop(self, monkeypatch):
        host = object()
        monkeypatch.setattr(mw_module, "get_registry", lambda: {})
        tabs = _FakeTabs()
        MainWindow._add_plugin_tabs(host, tabs, "left_tab_builders")
        assert tabs.added == []


# ─── _setup_menu 菜单注入 ──────────────────────────────────

class TestSetupMenuInjection:
    def _make_window(self, qtbot):
        class _MenuTestWindow(QMainWindow):
            def _toggle_theme(inner_self):
                MainWindow._toggle_theme(inner_self)

            def _update_theme_button(inner_self, theme):
                MainWindow._update_theme_button(inner_self, theme)

        win = _MenuTestWindow()
        qtbot.addWidget(win)
        # _setup_menu 只 connect 不调用这些处理器，桩为空函数即可
        for name in ("_open_settings_manager", "_open_user_manager",
                     "_open_scene_editor", "_open_reference_manager",
                     "_open_ocr_dialog", "_open_script_record", "_open_script_editor",
                     "_open_script_config",
                     "_open_batch_config", "_show_about", "_check_update",
                     "_open_docs", "_open_feedback"):
            setattr(win, name, lambda: None)
        return win

    def _menu_titles(self, win) -> list[str]:
        return [a.text() for a in win.menuBar().actions()]

    def test_plugin_menus_inserted_before_help(self, qtbot, monkeypatch):
        win = self._make_window(qtbot)
        calls = []

        def build_menu_a(host, menubar):
            calls.append((host, menubar))
            menubar.addMenu("插件A")

        def build_menu_b(host, menubar):
            menubar.addMenu("插件B")

        monkeypatch.setattr(mw_module, "get_registry", lambda: {
            "menu_builders": [build_menu_a, build_menu_b],
        })
        MainWindow._setup_menu(win)

        assert self._menu_titles(win) == ["通用", "工具", "插件A", "插件B", "帮助"]
        assert calls == [(win, win.menuBar())]
        corner = win.menuBar().cornerWidget()
        assert corner is not None
        assert corner.objectName() == "themeToggleButton"
        assert corner.toolTip() in {"切换到深色主题", "切换到浅色主题"}

    def test_menu_builder_exception_does_not_interrupt(self, qtbot, monkeypatch):
        win = self._make_window(qtbot)

        def boom(host, menubar):
            raise RuntimeError("menu builder 炸了")

        def build_ok(host, menubar):
            menubar.addMenu("好菜单")

        monkeypatch.setattr(mw_module, "get_registry", lambda: {
            "menu_builders": [boom, build_ok],
        })
        MainWindow._setup_menu(win)

        assert self._menu_titles(win) == ["通用", "工具", "好菜单", "帮助"]


# ─── F9 分发（f9_run 鸭子类型） ────────────────────────────

class _FakeLeftTabs:
    def __init__(self, widget):
        self._widget = widget

    def currentWidget(self):
        return self._widget


class _DispatchStub:
    """RunControlMixin._on_start 所需的最小属性集"""

    def __init__(self, widget, running: bool = False):
        self._running = running
        self._left_tabs = _FakeLeftTabs(widget)
        self.generic_runs = 0

    def _on_run_workflow(self):
        self.generic_runs += 1


class _TabWithF9:
    def __init__(self):
        self.f9_calls = 0

    def f9_run(self):
        self.f9_calls += 1


class TestF9Dispatch:
    def test_dispatches_to_tab_f9_run(self):
        tab = _TabWithF9()
        stub = _DispatchStub(tab)
        RunControlMixin._on_start(stub)
        assert tab.f9_calls == 1
        assert stub.generic_runs == 0

    def test_falls_back_to_generic_workflow(self):
        stub = _DispatchStub(object())  # 无 f9_run 的普通 Tab
        RunControlMixin._on_start(stub)
        assert stub.generic_runs == 1

    def test_ignored_when_running(self):
        tab = _TabWithF9()
        stub = _DispatchStub(tab, running=True)
        RunControlMixin._on_start(stub)
        assert tab.f9_calls == 0
        assert stub.generic_runs == 0

    def test_no_left_tabs_falls_back(self):
        stub = _DispatchStub(object())
        stub._left_tabs = None  # 对齐 Mixin 类属性兜底：页签未构建时为 None
        RunControlMixin._on_start(stub)
        assert stub.generic_runs == 1
