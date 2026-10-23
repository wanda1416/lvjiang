"""跨进程写锁（``core/fs_util.interprocess_lock``）与配置写锁顺序。

锁住四条会静默失效的性质：

- 锁目录只看目标路径，不看目标此刻是否存在——按 ``is_dir()`` 分支会让
  全新安装的 ``config/local`` 先落在父目录、后落在目录内部，分裂成两把锁、
  互斥直接消失；
- 同一线程嵌套获取可重入（写入链路里必然嵌套）；
- 另一个进程持锁时超时抛错，而不是把 UI 线程无声拖死；
- 真正跨进程的读改写不能丢更新（同一个进程内的两个实例测不出这一点）。

另有一条配置写锁顺序回归：``_configuration_write`` 走「线程锁 → 文件锁」，
``batch_changes`` / ``checked_write`` 也是这个顺序；反过来会互相等待而
永久死锁。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lvjiang.core import fs_util
from lvjiang.core.fs_util import InterprocessLockTimeout, interprocess_lock

_PRELUDE = """
import json, sys, time
from pathlib import Path
from lvjiang.core.fs_util import interprocess_lock
"""


def _run(script: str, *, cwd: Path, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """在子进程里跑脚本，保证 PYTHONPATH 指向本仓库的 src。"""
    env = {**os.environ}
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (src, env.get("PYTHONPATH"))))
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", _PRELUDE + script],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=timeout,
    )


# ─── 锁目录稳定性 ─────────────────────────────────────────

def test_lock_dir_does_not_depend_on_whether_target_exists(tmp_path):
    """目录目标在创建前后必须用同一个锁文件，否则互斥静默失效。"""
    directory = tmp_path / "local"
    assert not directory.exists()

    with interprocess_lock(directory, suffix="config"):
        pass
    before = sorted(p.name for p in (tmp_path / ".locks").glob("*.lock"))

    directory.mkdir()

    fs_util._LOCKS.clear()  # 等价于新进程：缓存为空
    with interprocess_lock(directory, suffix="config"):
        pass
    after = sorted(p.name for p in (tmp_path / ".locks").glob("*.lock"))

    assert before == after
    assert not (directory / ".locks").exists()


def test_lock_dir_can_be_placed_explicitly(tmp_path):
    """显式锁目录必须被采用，且不与默认位置混用。"""
    pad = tmp_path / "padded"
    with interprocess_lock(tmp_path / "a.json", suffix="x", lock_dir=pad):
        pass
    assert list(pad.glob("*.lock"))
    assert not (tmp_path / ".locks").exists()


def test_different_suffixes_use_different_lock_files(tmp_path):
    """同一路径换用途是两把锁；只按路径缓存会让后者复用前者的锁文件。"""
    target = tmp_path / "shared.json"
    with interprocess_lock(target, suffix="alpha"):
        pass
    fs_util._LOCKS.clear()
    with interprocess_lock(target, suffix="beta"):
        pass

    names = sorted(p.name for p in (tmp_path / ".locks").glob("*.lock"))
    assert len(names) == 2
    assert any(name.endswith(".alpha.lock") for name in names)
    assert any(name.endswith(".beta.lock") for name in names)


def test_nested_acquire_in_one_thread_is_reentrant(tmp_path):
    """写入链路里必然嵌套取锁：同线程嵌套不能自锁。"""
    target = tmp_path / "nested.json"
    with interprocess_lock(target, suffix="s"):
        with interprocess_lock(target, suffix="s"):
            with interprocess_lock(target, suffix="s"):
                pass


# ─── 跨进程互斥与超时 ─────────────────────────────────────

def test_other_process_holding_the_lock_makes_us_time_out(tmp_path):
    """另一个进程持锁时抛超时，而不是把调用线程无声拖死。"""
    target = tmp_path / "held.json"
    holder = threading.Thread(
        target=lambda: _run(
            f"with interprocess_lock(Path({str(target)!r}), suffix='s'):\n"
            f"    print('locked', flush=True)\n"
            f"    time.sleep(2.0)\n",
            cwd=tmp_path),
        daemon=True,
    )
    holder.start()
    try:
        deadline = time.monotonic() + 10
        lock_files = []
        while time.monotonic() < deadline:
            lock_files = list((tmp_path / ".locks").glob("*.lock"))
            if lock_files:
                break
            time.sleep(0.05)
        # 锁文件出现只说明创建过；再等一会儿让子进程真正持锁。
        time.sleep(0.5)

        with pytest.raises(InterprocessLockTimeout, match="超时"):
            with interprocess_lock(target, suffix="s", timeout=0.3):
                pass
    finally:
        holder.join(timeout=10)


def test_cross_process_read_modify_write_does_not_lose_updates(tmp_path):
    """两个进程各做多轮「读盘 → 加一 → 写回」，结果必须是完全累加。

    同进程内的两个 Store 实例共享同一把线程锁，测不出跨进程丢更新；这里
    必须真的起两个进程。
    """
    counter = tmp_path / "counter.json"
    counter.write_text(json.dumps({"value": 0}), encoding="utf-8")
    rounds = 5

    worker = (
        f"path = Path({str(counter)!r})\n"
        f"for _ in range({rounds}):\n"
        f"    with interprocess_lock(path, suffix='counter'):\n"
        f"        current = json.loads(path.read_text(encoding='utf-8'))\n"
        f"        time.sleep(0.01)\n"
        f"        path.write_text(json.dumps({{'value': current['value'] + 1}}),"
        f" encoding='utf-8')\n"
        f"print('done', flush=True)\n"
    )

    env = {**os.environ}
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (src, env.get("PYTHONPATH"))))
    env["PYTHONIOENCODING"] = "utf-8"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _PRELUDE + worker],
            cwd=str(tmp_path), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    for process in processes:
        _out, err = process.communicate(timeout=60)
        assert process.returncode == 0, err

    assert json.loads(counter.read_text(encoding="utf-8"))["value"] == rounds * 2


# ─── 配置写锁顺序 ─────────────────────────────────────────

def test_config_write_and_batch_write_do_not_deadlock(tmp_path):
    """``batch_changes``（线程锁→文件锁）与单文件写必须能并发完成。

    顺序反了就会：批量写入持线程锁等文件锁，单文件写持文件锁等线程锁，
    两边永久互等。这里用事件强制两者交叉进入临界区。
    """
    from lvjiang.core.config.resolver import ConfigResolver

    resolver = ConfigResolver(config_dir=tmp_path, dev_mode=False)
    resolver.local_dir.mkdir(parents=True, exist_ok=True)
    batch_entered = threading.Event()
    other_done = threading.Event()
    failures: list[BaseException] = []

    def batch_writer():
        try:
            with resolver.batch_changes():
                batch_entered.set()
                other_done.wait(5.0)      # 等另一线程真的进到写锁里
                resolver.save_merged("app.yaml", {"plans": [{"name": "batch"}]})
        except BaseException as exc:      # noqa: BLE001 - 断言时原样抛出
            failures.append(exc)

    def single_writer():
        try:
            batch_entered.wait(5.0)
            resolver.save_merged("app.yaml", {"plans": [{"name": "single"}]})
            other_done.set()
        except BaseException as exc:      # noqa: BLE001 - 断言时原样抛出
            failures.append(exc)

    first = threading.Thread(target=batch_writer, daemon=True)
    second = threading.Thread(target=single_writer, daemon=True)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not failures, failures
    assert not first.is_alive(), "批量写入没有完成：配置写锁出现死锁"
    assert not second.is_alive(), "单文件写入没有完成：配置写锁出现死锁"
