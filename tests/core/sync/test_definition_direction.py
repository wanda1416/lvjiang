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


def test_android_cannot_create_snapshot(monkeypatch):
    """refresh_snapshot 在 Android 上必须直接拒绝（PC 独占写权限）。"""
    from lvjiang.core.profile import snapshot_sync

    monkeypatch.setattr(snapshot_sync.sys, "getandroidapilevel", lambda: 34,
                        raising=False)
    report = snapshot_sync.refresh_snapshot("t")
    assert report.errors
    assert "PC 端" in report.errors[0]
    assert report.snapshots == 0


def test_no_snapshot_hint_points_to_pc_initial_sync():
    """无快照提示必须指向 PC 端初始同步（手机没有建快照能力）。"""
    from lvjiang.core.profile.snapshot_sync import NO_SNAPSHOT_HINT

    assert "PC 端" in NO_SNAPSHOT_HINT
    assert json.dumps(NO_SNAPSHOT_HINT, ensure_ascii=False)   # 可安全展示
