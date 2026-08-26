"""install_id 的生成、存储、重置：不含机器指纹 + 不落 session.json。"""
from __future__ import annotations

import json

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import identity as identity_mod


@pytest.fixture(autouse=True)
def isolated_telemetry_dir(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    reset_session_store()
    yield
    reset_session_store()


class TestGetIdentity:
    def test_generates_on_first_access(self):
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32
        assert all(c in "0123456789abcdef" for c in identity.install_id)
        assert identity.first_seen  # YYYY-MM-DD

    def test_stable_across_calls(self):
        a = identity_mod.get_identity()
        b = identity_mod.get_identity()
        assert a.install_id == b.install_id
        assert a.first_seen == b.first_seen

    def test_persisted_under_config_local_telemetry(self):
        identity_mod.get_identity()
        path = identity_mod.identity_path()
        assert path.exists()
        assert "config/local/telemetry" in str(path).replace("\\", "/")

    def test_never_lands_in_session_json(self, tmp_path):
        from lvjiang import constants
        identity = identity_mod.get_identity()
        session_path = constants.SESSION_PATH
        if session_path.exists():
            assert identity.install_id not in session_path.read_text(encoding="utf-8")

    def test_corrupted_file_regenerates_instead_of_crashing(self):
        path = identity_mod.identity_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{{", encoding="utf-8")
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32

    def test_truncated_install_id_rejected_and_regenerated(self):
        path = identity_mod.identity_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"install_id": "tooshort", "first_seen": "2026-01-01"}),
                        encoding="utf-8")
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32


class TestNoMachineFingerprint:
    """不含机器指纹的硬约束：即便这些 API 全部抛异常，也必须能正常生成 ID。"""

    def test_survives_uuid_getnode_failure(self, monkeypatch):
        import uuid
        monkeypatch.setattr(uuid, "getnode", lambda: (_ for _ in ()).throw(OSError("no mac")))
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32

    def test_survives_hostname_lookup_failure(self, monkeypatch):
        import socket
        monkeypatch.setattr(socket, "gethostname",
                            lambda: (_ for _ in ()).throw(OSError("no hostname")))
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32

    def test_survives_platform_node_failure(self, monkeypatch):
        import platform
        monkeypatch.setattr(platform, "node",
                            lambda: (_ for _ in ()).throw(OSError("no node")))
        identity = identity_mod.get_identity()
        assert len(identity.install_id) == 32

    def test_two_installs_get_different_ids(self, tmp_path, monkeypatch):
        from lvjiang import constants
        a = identity_mod.get_identity()
        monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config2")
        b = identity_mod.get_identity()
        assert a.install_id != b.install_id


class TestReset:
    def test_reset_produces_new_id(self):
        old = identity_mod.get_identity()
        new = identity_mod.reset_identity()
        assert new.install_id != old.install_id

    def test_reset_clears_spool(self):
        from lvjiang.core.telemetry import spool as spool_mod
        from lvjiang.core.telemetry.schema import EventSchema, FieldSpec

        schema = EventSchema(name="t", version=1, fields=(FieldSpec("x", str, choices=("a",)),))
        spool_mod.append(schema.validate({"x": "a"}))
        spool_mod.flush()
        assert spool_mod.take_batches(10)

        identity_mod.reset_identity()
        assert spool_mod.take_batches(10) == []


class TestPurge:
    def test_purge_removes_identity_file(self):
        identity_mod.get_identity()
        assert identity_mod.identity_path().exists()
        identity_mod.purge_identity()
        assert not identity_mod.identity_path().exists()

    def test_purge_on_missing_dir_does_not_raise(self):
        identity_mod.purge_identity()  # 目录本就不存在
