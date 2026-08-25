"""应用退出阶段的 Qt 对象释放回归测试。"""

from lvjiang import app as app_module


def test_dispose_qt_objects_before_interpreter_shutdown(monkeypatch):
    """窗口先于 QApplication 释放，且不会留下模块级 Qt 根对象。"""
    calls: list[object] = []

    class FakeApp:
        def processEvents(self) -> None:
            calls.append("process-events")

    window = object()
    qt_app = FakeApp()
    monkeypatch.setattr(app_module, "_hooks", [object()])
    monkeypatch.setattr(app_module, "_window", window)
    monkeypatch.setattr(app_module, "_app", qt_app)
    monkeypatch.setattr(
        app_module, "reset_theme_manager", lambda: calls.append("reset-theme"))
    monkeypatch.setattr(app_module.sip, "isdeleted", lambda _obj: False)
    monkeypatch.setattr(
        app_module.sip, "delete", lambda obj: calls.append(("delete", obj)))

    app_module._dispose_qt_objects()

    assert calls == [
        "reset-theme",
        ("delete", window),
        "process-events",
        ("delete", qt_app),
    ]
    assert app_module._hooks is None
    assert app_module._window is None
    assert app_module._app is None

