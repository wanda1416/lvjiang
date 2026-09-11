"""Launch/finish leases must outlive pause, stop requests and final persistence."""
from types import SimpleNamespace

import pytest

from lvjiang.core.access import AccessDeniedError, acquire_user
from lvjiang.core.config.users import SessionManager
from lvjiang.ui.main.execution_access import guarded_finish, guarded_launch


class Harness:
    def __init__(self, directory, username="alice"):
        self._session_manager = SessionManager(directory)
        self._user_manager = SimpleNamespace(get_active_user_name=lambda: username)
        self._running = False
        self.messages = []
        self.busy_messages = []
        self.log_text = SimpleNamespace(append=self.messages.append)
        self.history = []

    def _end_automation(self, name):
        self._running = False

    def sender(self):
        return None

    def _finish_task_run(self, worker, **kwargs):
        self.history.append(kwargs)

    def _show_user_execution_busy(self, message):
        self.busy_messages.append(message)

    @guarded_launch
    def launch(self, *, execution_username=None, abort=False, fail=False):
        if fail:
            raise ValueError("initialization failed")
        if abort:
            return
        self._execution_started = True
        self._running = True

    @guarded_finish
    def finish(self, *, fail=False):
        with pytest.raises(AccessDeniedError):
            acquire_user("alice", self._session_manager._users_dir)
        if fail:
            raise ValueError("save failed")

    @guarded_launch(user_selector="selector")
    def daily_launch(self):
        self._execution_started = True
        self._running = True


@pytest.mark.parametrize("fail", [False, True])
def test_lease_is_held_through_final_save_and_released_on_failure(tmp_path, fail):
    first = Harness(tmp_path)
    first.launch()
    second = Harness(tmp_path)
    second.launch()
    assert not second._running
    assert second.messages
    assert second.busy_messages == ["用户「alice」正在执行任务，请稍后重试"]
    other = Harness(tmp_path, "bob")
    other.launch()
    assert other._running
    other._execution_lease.release()
    first.finish(fail=fail)
    assert not first._running
    if fail:
        assert first.history[-1]["status"] == "failed"
    second.launch()
    assert second._running
    second.finish()


@pytest.mark.parametrize("kwargs", [{"abort": True}, {"fail": True}])
def test_startup_rejection_does_not_leak_user_lock(tmp_path, kwargs):
    first = Harness(tmp_path)
    first.launch(**kwargs)
    lease = acquire_user("alice", tmp_path)
    lease.release()


def test_daily_execution_selector_not_global_active_user_owns_lock(tmp_path):
    harness = Harness(tmp_path, "alice")
    harness.selector = SimpleNamespace(resolve_username=lambda: "bob")
    harness.daily_launch()
    try:
        assert harness._execution_username_snapshot == "bob"
        with pytest.raises(AccessDeniedError):
            acquire_user("bob", tmp_path)
        alice = acquire_user("alice", tmp_path)
        alice.release()
    finally:
        harness._execution_lease.release()
