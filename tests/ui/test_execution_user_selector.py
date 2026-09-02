"""Process-local execution-user selection and runtime binding."""

from types import SimpleNamespace

from lvjiang.ui.execution_user_selector import ExecutionUserSelector
from lvjiang.ui.main.run_control import RunControlMixin


class _Users:
    def __init__(self):
        self.users = ["当前用户", "固定用户"]
        self.active = "当前用户"

    def list_users(self):
        return list(self.users)

    def get_active_user_name(self):
        return self.active


def test_selector_defaults_to_follow_current_on_each_construction(qtbot):
    users = _Users()
    first = ExecutionUserSelector(users)
    qtbot.addWidget(first)

    assert first.combo.currentText() == "跟随当前用户"
    assert first.resolve_username() == "当前用户"
    users.active = "固定用户"
    assert first.resolve_username() == "固定用户"

    first.combo.setCurrentIndex(first.combo.findData("当前用户"))
    second = ExecutionUserSelector(users)
    qtbot.addWidget(second)
    assert second.combo.currentText() == "跟随当前用户"


def test_fixed_user_survives_refresh_but_deleted_user_is_rejected(qtbot):
    users = _Users()
    selector = ExecutionUserSelector(users)
    qtbot.addWidget(selector)
    selector.combo.setCurrentIndex(selector.combo.findData("固定用户"))

    selector.refresh_users()
    assert selector.resolve_username() == "固定用户"

    users.users.remove("固定用户")
    # Even before the UI refresh, a stale fixed user must never silently run as
    # somebody else.
    assert selector.resolve_username() == ""
    selector.refresh_users()
    assert selector.combo.currentText() == "跟随当前用户"


def test_runtime_binding_uses_explicit_execution_user():
    loaded = {"owner": "固定用户"}
    save_calls = []
    sessions = SimpleNamespace(
        load=lambda username: loaded if username == "固定用户" else {},
        save_fn=lambda username, session: save_calls.append(
            (username, session)) or (lambda: None),
    )
    host = SimpleNamespace(_session_manager=sessions)
    engine = SimpleNamespace(session=None, run_username="", _save_callback=None)

    RunControlMixin._bind_engine_user(host, engine, "固定用户")

    assert engine.run_username == "固定用户"
    assert engine.session is loaded
    assert save_calls == [("固定用户", loaded)]
