"""手动「同步全部头像」：双向合并、时间裁决、1MB 与批次上限。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta


class _Fake:
    """最小云端：名册行带 avatar_sha256；头像按用户名存取。"""

    def __init__(self):
        self.roster: list[dict] = []
        self.avatars: dict[str, dict] = {}          # username → {sha256, payload, updated_at}
        self.puts: list[str] = []
        self.batches: list[list[str]] = []

    def post(self, route, data, token):
        if route == "/api/sync/users/pull":
            return {"users": self.roster}
        if route == "/api/sync/users/avatars/pull":
            names = list(data.get("usernames") or [])
            self.batches.append(names)
            return {"avatars": [
                {"username": name, "sha256": self.avatars[name]["sha256"],
                 "updated_at": self.avatars[name]["updated_at"],
                 "payload_base64": base64.b64encode(
                     self.avatars[name]["payload"]).decode()}
                for name in names if name in self.avatars]}
        if route == "/api/sync/users/avatar/get":
            name = data.get("username")
            item = self.avatars.get(name)
            if not item:
                return {"avatar": None}
            return {"avatar": {"payload_base64": base64.b64encode(item["payload"]).decode(),
                              "sha256": item["sha256"], "updated_at": item["updated_at"]}}
        if route == "/api/sync/users/avatar/put":
            name = data["username"]
            self.puts.append(name)
            self.avatars[name] = {
                "sha256": data["sha256"],
                "payload": base64.b64decode(data["payload_base64"]),
                "updated_at": datetime.now().isoformat(timespec="seconds")}
            return {"username": name, "sha256": data["sha256"]}
        raise AssertionError(route)


def _env(tmp_path, monkeypatch):
    from lvjiang.core.sync import users_sync

    fake = _Fake()
    users = tmp_path / "users"
    avatars = tmp_path / "avatars"
    users.mkdir()
    avatars.mkdir()
    monkeypatch.setattr(users_sync, "post", fake.post)
    monkeypatch.setattr(users_sync, "_users_dir", lambda: users)
    monkeypatch.setattr(users_sync, "_roster", lambda: ["甲", "乙"])
    monkeypatch.setattr("lvjiang.constants.AVATARS_DIR", avatars)
    return fake, users, avatars


def _user(users, name: str, avatar: str = "") -> None:
    (users / f"{name}.json").write_text(
        json.dumps({"avatar": avatar}, ensure_ascii=False), encoding="utf-8")


def _seed_cloud(fake, name: str, payload: bytes, *, minutes_ago: int = 0) -> str:
    sha = hashlib.sha256(payload).hexdigest()
    fake.avatars[name] = {
        "sha256": sha, "payload": payload,
        "updated_at": (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(
            timespec="seconds")}
    fake.roster.append({"username": name, "avatar_sha256": sha})
    return sha


def test_local_only_avatar_is_uploaded(tmp_path, monkeypatch):
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    _user(users, "乙")
    (avatars / "a.png").write_bytes(b"AAA")
    fake.roster = [{"username": "甲"}, {"username": "乙"}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.uploaded == 1 and report.downloaded == 0
    assert fake.puts == ["甲"]


def test_cloud_only_avatar_is_downloaded(tmp_path, monkeypatch):
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    _seed_cloud(fake, "甲", b"CLOUD")
    fake.roster = [{"username": "甲", "avatar_sha256": fake.avatars["甲"]["sha256"]}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.downloaded == 1
    assert (avatars / "a.png").read_bytes() == b"CLOUD"


def test_same_sha_is_skipped(tmp_path, monkeypatch):
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    (avatars / "a.png").write_bytes(b"SAME")
    _seed_cloud(fake, "甲", b"SAME")
    fake.roster = [{"username": "甲", "avatar_sha256": fake.avatars["甲"]["sha256"]}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.skipped == 1 and report.uploaded == 0 and report.downloaded == 0


def test_newer_cloud_wins_on_conflict(tmp_path, monkeypatch):
    """两边都变：云端更新 → 下载覆盖本地。"""
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    local = avatars / "a.png"
    local.write_bytes(b"LOCAL")
    old = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(local, (old, old))
    _seed_cloud(fake, "甲", b"CLOUD", minutes_ago=5)
    fake.roster = [{"username": "甲", "avatar_sha256": fake.avatars["甲"]["sha256"]}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.downloaded == 1 and report.uploaded == 0
    assert local.read_bytes() == b"CLOUD"


def test_newer_local_wins_on_conflict(tmp_path, monkeypatch):
    """两边都变：本地更新 → 上传覆盖云端。"""
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    local = avatars / "a.png"
    local.write_bytes(b"LOCAL")
    _seed_cloud(fake, "甲", b"CLOUD", minutes_ago=120)
    fake.roster = [{"username": "甲", "avatar_sha256": fake.avatars["甲"]["sha256"]}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.uploaded == 1 and report.downloaded == 0
    assert fake.avatars["甲"]["payload"] == b"LOCAL"


def test_oversized_avatar_is_rejected(tmp_path, monkeypatch):
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    _user(users, "甲", "a.png")
    (avatars / "a.png").write_bytes(b"X" * 1_000_001)
    fake.roster = [{"username": "甲"}]

    report = sync_all_avatars("t", users_dir=users)

    assert report.uploaded == 0
    assert any("1MB" in error for error in report.errors)
    assert fake.puts == []


def test_download_batches_are_capped_at_ten(tmp_path, monkeypatch):
    """一次性同步 12 张缺失头像 → 分成 10 + 2 两批请求。"""
    from lvjiang.core.sync.users_sync import sync_all_avatars

    fake, users, avatars = _env(tmp_path, monkeypatch)
    names = [f"用户{i:02d}" for i in range(12)]
    fake.roster = []
    for name in names:
        _user(users, name, f"{name}.png")
        sha = _seed_cloud(fake, name, name.encode())
        fake.roster.append({"username": name, "avatar_sha256": sha})
    monkeypatch.setattr("lvjiang.core.sync.users_sync._roster", lambda: names)

    report = sync_all_avatars("t", users_dir=users)

    assert report.downloaded == 12
    assert [len(batch) for batch in fake.batches] == [10, 2]
