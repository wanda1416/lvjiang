"""account.json 的唯一写入口：并发写不同键时谁也不能抹掉别人。

事故面：account.json 里住着登录态（login / account_id / e2e_key 缓存）、profile
水位、document_sync、users_sync、sync_paused 五类键，却各有各的写入者。此前每个
写入者各自「读整份 → 改一个键 → 整份写回」，其中两条还是非原子的 ``write_text``：

- 并发时后写覆盖前写，水位/脏标记互相吞掉；
- 读到半截 JSON 时 ``load_profile`` 返回 ``{}``，下一次写回就把 login / account_id
  / e2e_key 整份抹成只剩自己那一段。

现在全部经 ``service.mutate_profile``（进程内线程锁 + 跨进程文件锁 + 原子写）。
"""
from __future__ import annotations

import json
import threading
import time

from lvjiang.core.account import service

_SEED = {"login": "wang", "account_id": "a" * 32, "display_name": "王",
         "e2e_key": "11" * 32, "profile_sync": {"remote_history_id": 7}}


def _seed(tmp_path, monkeypatch) -> object:
    path = tmp_path / "account.json"
    monkeypatch.setattr(service, "account_config_path", lambda: path)
    path.write_text(json.dumps(_SEED, ensure_ascii=False), encoding="utf-8")
    return path


def test_mutate_profile_serializes_read_modify_write(tmp_path, monkeypatch) -> None:
    """两个写入者必须串行：拿同一份磁盘快照各改各的键，后写的会抹掉前一个。"""
    path = _seed(tmp_path, monkeypatch)
    inside = threading.Event()
    release = threading.Event()

    def slow(profile: dict) -> None:
        profile["users_sync"] = {"sig": "a"}
        inside.set()
        release.wait(2.0)              # 持锁期间卡住，给第二个写入者插队的机会

    def fast(profile: dict) -> None:
        profile["sync_paused"] = True

    first = threading.Thread(target=lambda: service.mutate_profile(slow))
    first.start()
    assert inside.wait(2.0)
    second = threading.Thread(target=lambda: service.mutate_profile(fast))
    second.start()
    time.sleep(0.2)
    release.set()
    first.join(10)
    second.join(10)

    profile = json.loads(path.read_text(encoding="utf-8"))
    assert profile["users_sync"] == {"sig": "a"}     # 被后者抹掉就是丢锁
    assert profile["sync_paused"] is True
    assert profile["login"] == "wang"
    assert profile["e2e_key"] == "11" * 32


def test_concurrent_state_writers_keep_login_state(tmp_path, monkeypatch) -> None:
    """并发写水位与暂停开关时，登录态与密钥缓存不能被整份覆盖抹掉。"""
    path = _seed(tmp_path, monkeypatch)

    def pump_sync_state() -> None:
        for index in range(40):
            service.save_profile_sync_state({"remote_history_id": index})

    def pump_paused() -> None:
        for index in range(40):
            service.set_sync_paused(bool(index % 2))

    threads = [threading.Thread(target=pump_sync_state),
               threading.Thread(target=pump_paused),
               threading.Thread(target=pump_sync_state)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)

    profile = json.loads(path.read_text(encoding="utf-8"))
    assert profile["login"] == "wang"
    assert profile["account_id"] == "a" * 32
    assert profile["e2e_key"] == "11" * 32
    assert "profile_sync" in profile
    assert "sync_paused" in profile


def test_mutate_profile_sees_latest_disk_state(tmp_path, monkeypatch) -> None:
    """回调拿到的必须是**锁内重读**的最新磁盘内容，不是调用前的旧快照。"""
    path = _seed(tmp_path, monkeypatch)
    # 模拟「另一个进程刚写过」：调用方尚未读，磁盘已变
    path.write_text(json.dumps({**_SEED, "users_sync": {"sig": "other"}},
                               ensure_ascii=False), encoding="utf-8")
    seen: dict = {}

    def record(profile: dict) -> None:
        seen.update(profile)

    assert service.mutate_profile(record) is True
    assert seen["users_sync"] == {"sig": "other"}


def test_mutate_profile_can_refuse_when_file_is_missing(tmp_path, monkeypatch) -> None:
    """脏标记这类写入绝不能凭空造一份空账号态出来（``require_existing``）。"""
    path = tmp_path / "account.json"
    monkeypatch.setattr(service, "account_config_path", lambda: path)
    called: list[int] = []

    assert service.mutate_profile(lambda profile: called.append(1),
                                  require_existing=True) is False
    assert called == []
    assert not path.exists()
