"""定义通道方向控制与快照创建权限（镜像端只读）。"""
from __future__ import annotations

import base64
import gzip
import json


def _definition_payload(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")


def test_mirror_end_never_pushes_definition(monkeypatch, tmp_path):
    """镜像端（Android）本地 yaml 与云端不同时，只能拉云端，绝不能反推。"""
    from lvjiang.core.sync import definition_sync

    puts: list[dict] = []
    remote_text = "quota:\n  - key: from_cloud\n"

    def fake_post(route, data, token):
        if data:
            puts.append(data)
            return {"sha256": "pushed"}
        return {"definition": {"payload_base64": _definition_payload(remote_text),
                               "zipped": True, "sha256": "cloud-sha"}}

    monkeypatch.setattr(definition_sync, "post", fake_post)
    monkeypatch.setattr(definition_sync, "profile_sync_state", lambda: {})
    monkeypatch.setattr(definition_sync, "save_profile_sync_state", lambda s: None)
    monkeypatch.setattr("lvjiang.core.profile.schema.reload_profile_config", lambda: None)

    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("quota:\n  - key: from_bundle\n", encoding="utf-8")

    report = definition_sync.sync_definition("t", yaml_path, allow_push=False)

    assert puts == []                                    # 没有上行
    assert report.pulled == 1
    assert "from_cloud" in yaml_path.read_text(encoding="utf-8")


def test_mirror_end_keeps_local_when_cloud_has_no_definition(monkeypatch, tmp_path):
    """云端还没有定义时，镜像端不能创建它。"""
    from lvjiang.core.sync import definition_sync

    puts: list[dict] = []

    def fake_post(route, data, token):
        if data:
            puts.append(data)
            return {}
        return {"definition": None}

    monkeypatch.setattr(definition_sync, "post", fake_post)
    monkeypatch.setattr(definition_sync, "profile_sync_state", lambda: {})
    monkeypatch.setattr(definition_sync, "save_profile_sync_state", lambda s: None)

    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("quota:\n  - key: from_bundle\n", encoding="utf-8")

    report = definition_sync.sync_definition("t", yaml_path, allow_push=False)

    assert puts == []
    assert report.errors == []
    assert "from_bundle" in yaml_path.read_text(encoding="utf-8")


def test_pc_end_still_pushes_when_cloud_differs(monkeypatch, tmp_path):
    """PC（allow_push 默认 True）保持原有上行语义。"""
    from lvjiang.core.sync import definition_sync

    puts: list[dict] = []

    def fake_post(route, data, token):
        if data:
            puts.append(data)
            return {"sha256": "new"}
        return {"definition": {"payload_base64": _definition_payload("quota: []\n"),
                               "zipped": True, "sha256": "old-sha"}}

    monkeypatch.setattr(definition_sync, "post", fake_post)
    monkeypatch.setattr(definition_sync, "profile_sync_state", lambda: {})
    monkeypatch.setattr(definition_sync, "save_profile_sync_state", lambda s: None)

    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text("quota:\n  - key: local_new\n", encoding="utf-8")

    report = definition_sync.sync_definition("t", yaml_path)

    assert len(puts) == 1
    assert report.pushed == 1


def test_android_cannot_overwrite_existing_snapshot(monkeypatch):
    """云端已有快照时，Android 不允许覆盖（PC 独占覆盖权）。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync.sys, "getandroidapilevel", lambda: 34, raising=False)
    monkeypatch.setattr(snapshot_sync, "remote_snapshots", lambda token: [{"id": 1}])
    report = snapshot_sync.refresh_snapshot("t")
    assert report.errors
    assert "PC" in report.errors[0]
    assert report.snapshots == 0


def test_android_may_seed_first_baseline(monkeypatch):
    """云端一份快照都没有时，Android 允许建立首份基线（否则纯手机用户永远没有基线）。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync.sys, "getandroidapilevel", lambda: 34, raising=False)
    monkeypatch.setattr(snapshot_sync, "remote_snapshots", lambda token: [])
    called = {}

    def fake_pull(token, **kwargs):
        called["pull"] = True
        return snapshot_sync.ProfileSyncReport()

    monkeypatch.setattr(snapshot_sync, "pull_and_replay", fake_pull)
    monkeypatch.setattr(snapshot_sync, "_snapshot_entries", lambda: ([], 0))
    report = snapshot_sync.refresh_snapshot("t")
    assert called.get("pull"), "应当走到上传流程，而不是被平台门禁拦下"
    assert report.errors == ["本机没有可上传的 profile 数据"]


def test_no_snapshot_hint_points_to_pc_initial_sync():
    """无快照提示必须指向 PC 端初始同步（手机没有建快照能力）。"""
    from lvjiang.core.profile.snapshot_sync import NO_SNAPSHOT_HINT

    assert "PC 端" in NO_SNAPSHOT_HINT
    assert json.dumps(NO_SNAPSHOT_HINT, ensure_ascii=False)   # 可安全展示


def test_sync_all_seeds_first_baseline_when_cloud_empty(monkeypatch):
    """云端一份快照都没有、本机有数据时，一轮同步末尾自动建立首份基线。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync, "profile_protocol_block", lambda token: "")
    monkeypatch.setattr(snapshot_sync, "pull_and_replay",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport())
    monkeypatch.setattr(snapshot_sync, "push_local",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport(pushed=2))
    monkeypatch.setattr(snapshot_sync, "local_profile_has_entries", lambda: True)
    monkeypatch.setattr(snapshot_sync, "_state", lambda: {"cloud_has_snapshot": False})
    calls = {}

    def fake_refresh(token, **kw):
        calls["refresh"] = True
        return snapshot_sync.ProfileSyncReport(snapshots=1)

    monkeypatch.setattr(snapshot_sync, "refresh_snapshot", fake_refresh)
    report = snapshot_sync.sync_all("t")
    assert calls.get("refresh") is True
    assert report.snapshots == 1
    assert report.pushed == 2


def test_sync_all_skips_seeding_when_baseline_known(monkeypatch):
    """云端已有快照时不再建基线（覆盖是另一回事），也不额外请求快照清单。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync, "profile_protocol_block", lambda token: "")
    monkeypatch.setattr(snapshot_sync, "pull_and_replay",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport())
    monkeypatch.setattr(snapshot_sync, "push_local",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport())
    monkeypatch.setattr(snapshot_sync, "local_profile_has_entries", lambda: True)
    monkeypatch.setattr(snapshot_sync, "_state", lambda: {"cloud_has_snapshot": True})
    monkeypatch.setattr(snapshot_sync, "refresh_snapshot",
                        lambda token, **kw: (_ for _ in ()).throw(AssertionError("不应建基线")))
    report = snapshot_sync.sync_all("t")
    assert report.errors == []


def test_sync_all_skips_seeding_without_local_entries(monkeypatch):
    """本机没有数据时不建空基线：空基线等于把「没数据」固化成全网基线。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync, "profile_protocol_block", lambda token: "")
    monkeypatch.setattr(snapshot_sync, "pull_and_replay",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport())
    monkeypatch.setattr(snapshot_sync, "push_local",
                        lambda token, **kw: snapshot_sync.ProfileSyncReport())
    monkeypatch.setattr(snapshot_sync, "local_profile_has_entries", lambda: False)
    monkeypatch.setattr(snapshot_sync, "refresh_snapshot",
                        lambda token, **kw: (_ for _ in ()).throw(AssertionError("不应建基线")))
    report = snapshot_sync.sync_all("t")
    assert report.errors == []

