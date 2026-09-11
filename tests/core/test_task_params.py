import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.config.wf_configs import set_wf_config
from lvjiang.core.task_params import resolve_task_params
from lvjiang.core.user_config import User, save_user_metadata, set_user_workflow_params

PARAMETERS = [
    {"name": "count", "type": "number", "default": 3},
    {"name": "enabled", "type": "bool", "default": True},
]


@pytest.fixture
def session_store(tmp_path, monkeypatch):
    from lvjiang import constants

    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    yield
    reset_session_store()


def test_shared_task_params_include_defaults(session_store, tmp_path):
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)
    set_wf_config("task", {"count": "8", "foreign": "ignored"})

    params, source = resolve_task_params("task", "alice", PARAMETERS, users_dir)

    assert params == {"count": "8", "enabled": True}
    assert source == "global"


def test_user_override_is_partial_and_explicit(session_store, tmp_path):
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)
    set_wf_config("task", {"count": "8", "enabled": True})
    set_user_workflow_params("alice", "task", {"enabled": False}, users_dir)

    params, source = resolve_task_params("task", "alice", PARAMETERS, users_dir)

    assert params == {"count": "8", "enabled": False}
    assert source == "user"
