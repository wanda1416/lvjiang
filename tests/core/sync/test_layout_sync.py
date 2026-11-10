"""页面结构上传/下载：覆盖确认、乐观锁与下载前的校验与备份。

保护三条真实回归：

* 远端结构来自别的实例时不得静默覆盖（必须先问用户），本实例自己写的则直接覆盖；
* 界面上「确认覆盖」这段时间里别人写过，服务端 409 要变成再次询问而不是覆盖；
* 下载必须先校验再动文件：远端内容坏了就一个字节都不改，成功时留下备份。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json

import pytest

from lvjiang.core.account.identity import stable_instance_id
from lvjiang.core.profile import layout
from lvjiang.core.sync import layout_sync
from lvjiang.core.sync.client import SyncConflict, SyncError


class FakeLayoutServer:
    """按服务端语义模拟：来源由会话决定，`expected_sha` 不符返回 409 冲突。"""

    def __init__(self) -> None:
        self.payload: bytes | None = None
        self.sha = ""
        self.origin_instance = ""
        self.origin_name = "书房PC"
        self.puts = 0

    def meta(self, *, with_payload: bool) -> dict:
        meta = {
            "sha256": self.sha,
            "updated_at": "2026-11-11T00:00:00",
            "size": len(self.payload or b""),
            "origin_instance_id": self.origin_instance,
            "origin_name": self.origin_name,
            "platform": "win32",
        }
        if with_payload and self.payload is not None:
            meta["payload_base64"] = base64.b64encode(self.payload).decode()
            meta["zipped"] = True
        return meta

    def post(self, route: str, data: dict, token: str) -> dict:
        assert route == "/api/sync/profile/layout"
        if "payload_base64" not in data:
            remote = self.meta(with_payload=True) if self.payload is not None else None
            return {"layout": remote}
        expected = data.get("expected_sha")
        if expected is not None and expected != self.sha:
            raise SyncConflict({"error": "conflict", "layout": self.meta(with_payload=False)})
        self.payload = base64.b64decode(data["payload_base64"])
        self.sha = data["sha256"]
        self.origin_instance = stable_instance_id()
        self.puts += 1
        return {"layout": self.meta(with_payload=False)}


@pytest.fixture
def server(monkeypatch):
    fake = FakeLayoutServer()
    monkeypatch.setattr(layout_sync, "post", fake.post)
    return fake


def _local_doc(columns: list[str]) -> None:
    layout.save(layout.ProfileLayout(overview=layout.OverviewLayout(
        group_order=["甲组"],
        groups={"甲组": layout.GroupLayout(columns=columns, name_column_width=90)},
    )))


def test_upload_refuses_to_overwrite_another_instance(server):
    """远端是别的实例写的：先报冲突，把远端信息交给界面去问用户。"""
    _local_doc(["a"])
    server.payload = b"old"
    server.sha = "remote-sha"
    server.origin_instance = "another-instance"

    with pytest.raises(layout_sync.LayoutConflict) as excinfo:
        layout_sync.upload("token")

    conflict = excinfo.value
    assert conflict.remote is not None
    assert conflict.remote.origin_name == "书房PC"
    assert not conflict.remote.is_from_this_instance()
    assert server.puts == 0
    # 本机文件没有被改动。
    assert layout.load().overview.groups["甲组"].columns == ["a"]


def test_upload_force_overwrites_and_reports_origin(server):
    """用户确认覆盖后写入成功，远端来源变成这台实例。"""
    _local_doc(["a"])
    server.payload = b"old"
    server.sha = "remote-sha"
    server.origin_instance = "another-instance"

    outcome = layout_sync.upload("token", force=True)

    assert outcome.status == "uploaded"
    assert server.puts == 1
    assert server.sha == layout_sync.local_sha256()
    assert outcome.remote is not None and outcome.remote.is_from_this_instance()


def test_upload_same_instance_overwrites_without_conflict(server):
    """远端就是本实例写的：直接覆盖，不再问一次。"""
    _local_doc(["a"])
    server.payload = b"old"
    server.sha = "remote-sha"
    server.origin_instance = stable_instance_id()

    assert layout_sync.upload("token").status == "uploaded"
    assert server.puts == 1


def test_upload_reports_unchanged_when_sha_matches(server):
    """云端与本机内容一致时什么都不写。"""
    _local_doc(["a"])
    server.payload = b"same"
    server.sha = layout_sync.local_sha256() or ""
    server.origin_instance = "another-instance"

    assert layout_sync.upload("token").status == "unchanged"
    assert server.puts == 0


def test_upload_conflict_when_remote_changed_during_confirmation(server):
    """确认期间别人写过：服务端 409 → 再次询问，不覆盖。"""
    _local_doc(["a"])
    server.payload = b"old"
    server.sha = "remote-sha"
    server.origin_instance = stable_instance_id()

    def racing_post(route: str, data: dict, token: str) -> dict:
        # 模拟「用户看确认框时另一台设备先写成功」：只在写入那一刻换掉远端 sha。
        if "payload_base64" in data:
            server.sha = "newer-sha"
        return FakeLayoutServer.post(server, route, data, token)

    layout_sync.post = racing_post  # type: ignore[assignment]
    with pytest.raises(layout_sync.LayoutConflict) as excinfo:
        layout_sync.upload("token")

    assert "改写" in str(excinfo.value)
    assert server.puts == 0


def test_upload_rejects_when_local_structure_missing(server):
    with pytest.raises(SyncError, match="还没有页面结构"):
        layout_sync.upload("token")


def test_download_validates_before_touching_local_file(server):
    """远端内容不合法时一个字节都不改，也不留备份。"""
    _local_doc(["a"])
    before = layout.layout_path().read_bytes()
    # 合法 gzip、内容却不是 JSON：走「解析校验」这一关，而不是解码失败。
    server.payload = gzip.compress(b"not-json")
    server.sha = "broken"
    server.origin_instance = "another-instance"

    with pytest.raises(ValueError, match="JSON"):
        layout_sync.download("token")

    assert layout.layout_path().read_bytes() == before
    assert not list(layout.layout_path().parent.glob("_backup-*"))


def test_download_backs_up_and_replaces(server):
    """下载成功：先备份旧结构，再写入新结构。"""
    _local_doc(["a"])
    remote_doc = json.dumps(
        {"version": 1, "overview": {"group_order": ["乙组"],
                                    "groups": {"乙组": {"columns": ["b"], "show_totals": True}}}},
        ensure_ascii=False,
    ).encode("utf-8")
    server.payload = gzip.compress(remote_doc)
    server.sha = hashlib.sha256(remote_doc).hexdigest()
    server.origin_instance = "another-instance"

    remote, backup = layout_sync.download("token")

    assert remote.short_sha == server.sha[:8]
    assert backup is not None and backup.name == "profile-layout-before.json"
    # 备份里是替换前的结构（旧分组 甲组 / 列 a 仍在）。
    assert json.loads(backup.read_text(encoding="utf-8"))["overview"]["groups"]["甲组"]["columns"] == ["a"]
    doc = layout.load()
    assert doc.overview.groups["乙组"].columns == ["b"]
    assert doc.overview.groups["乙组"].show_totals is True
