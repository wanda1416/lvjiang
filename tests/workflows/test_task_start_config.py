"""任务起点只丢 YAML 读缓存，不下载、不安装、不加配置锁。"""
from contextlib import contextmanager

from lvjiang.core.config import content, remote
from tests.workflows.conftest import make_engine


def test_task_start_does_not_install_or_lock(tmp_path, monkeypatch):
    hit = []
    monkeypatch.setattr(remote, "start_background_sync", lambda **_kwargs: hit.append("sync"))
    monkeypatch.setattr(remote, "install_pending", lambda **_kwargs: hit.append("install"))
    monkeypatch.setattr(remote, "prepare_startup_configuration", lambda: hit.append("prepare"))
    monkeypatch.setattr(remote, "recover_installation", lambda: hit.append("recover"))
    monkeypatch.setattr(remote, "apply_outcome", lambda *_args, **_kwargs: hit.append("apply"))

    @contextmanager
    def locked(*_args, **_kwargs):
        hit.append("lock")
        yield True

    monkeypatch.setattr(content, "configuration_lock", locked)
    workflow = tmp_path / "task.wf"
    workflow.write_text('log "ok"\n', encoding="utf-8")
    make_engine()._execute_unlocked(workflow)
    assert hit == []
