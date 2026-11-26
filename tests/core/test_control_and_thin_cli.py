"""本地控制面与纯标准库薄壳 lvjiang-cli 端到端测试。"""
from __future__ import annotations

import ast
import json
import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

from lvjiang import cli_client
from lvjiang.core.control import (
    ControlEndpoint,
    ControlServer,
    read_control_file,
    remove_control_file,
    write_control_file,
)


def test_cli_client_uses_only_python_stdlib():
    """硬约束：薄壳 cli_client.py 不得导入任何第三方库或 lvjiang 内部模块。"""
    src = Path(cli_client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    stdlib = set(sys.stdlib_module_names)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"薄壳 CLI 禁止相对导入: level={node.level}"
            if node.module:
                imported.add(node.module.split(".")[0])
    non_stdlib = imported - stdlib
    assert not non_stdlib, f"cli_client.py 引入了非标准库依赖: {sorted(non_stdlib)}"


def test_control_file_atomic_permissions_and_token_guarded_delete(tmp_path: Path):
    """control.json 原子写入、POSIX 0o600 权限与按 token 保护清理。"""
    ctrl_path = tmp_path / "config" / "session" / "control.json"
    ep1 = ControlEndpoint(
        pid=12345,
        instance_id="inst-a",
        port=45678,
        token="tok-a",
        mode="headless",
        started_at="2026-11-26T00:00:00Z",
    )
    write_control_file(ep1, ctrl_path)
    loaded = read_control_file(ctrl_path)
    assert loaded == ep1

    if os.name != "nt":
        mode = stat.S_IMODE(ctrl_path.stat().st_mode)
        assert mode == 0o600

    # token 不匹配时不得误删新实例的 control.json
    remove_control_file(ctrl_path, expected_token="wrong-token")
    assert ctrl_path.is_file()

    # token 匹配时正常清理
    remove_control_file(ctrl_path, expected_token="tok-a")
    assert not ctrl_path.exists()


def test_control_server_auth_busy_fail_and_wait_retry(tmp_path: Path, capsys):
    """验证 Bearer 鉴权、409 busy、--on-busy fail 立即退出与 --on-busy wait 客户端重试。"""
    ctrl_path = tmp_path / "config" / "session" / "control.json"
    gate = threading.Event()
    started_first = threading.Event()
    calls: list[dict[str, Any]] = []

    def fake_runner(payload, stop_event, emit):
        calls.append(dict(payload))
        emit("log", {"level": "INFO", "time": "12:00:00", "message": f"start {payload.get('script_id') or payload.get('name')}"})
        if payload.get("script_id") == "slow_job":
            started_first.set()
            gate.wait(timeout=5.0)
        return {"ok": True, "code": 0, "ran": payload.get("script_id") or payload.get("name")}

    srv = ControlServer(
        mode="headless",
        control_path=ctrl_path,
        instance_id="test-inst",
        job_runner=fake_runner,
        catalog_provider=lambda **_kw: {
            "ok": True,
            "scripts": [
                {"id": "daily_1", "name": "日常一", "kind": "wf"},
                {"id": "auto_tuning", "name": "自动调律", "kind": "class"},
            ],
            "batches": [
                {"id": "b1", "name": "日常全清", "kind": "batch", "task_ids": ["daily_1"]},
            ],
        },
    )
    ep = srv.start()
    try:
        # 1. 错误 token 返回 401
        status, _ = cli_client._http_json(ep.port, "bad-token", "GET", "/control/health")
        assert status == 401

        # 2. list 查询覆盖 wf / class / batch
        rc = cli_client.main(["list", "--root", str(tmp_path), "--json"])
        assert rc == cli_client.EXIT_OK
        cat = json.loads(capsys.readouterr().out)
        assert [s["kind"] for s in cat["scripts"]] == ["wf", "class"]
        assert cat["batches"][0]["name"] == "日常全清"

        # 3. 兼容旧版 --list
        rc_legacy = cli_client.main(["--list", "--root", str(tmp_path), "--json"])
        assert rc_legacy == cli_client.EXIT_OK
        capsys.readouterr()

        # 4. 占住唯一执行槽
        t_first = threading.Thread(
            target=lambda: cli_client.main(
                ["run", "--id", "slow_job", "--root", str(tmp_path), "--quiet"]
            ),
            daemon=True,
        )
        t_first.start()
        assert started_first.wait(timeout=3.0)

        # 5. --on-busy fail 立刻返回 EXIT_BUSY (7)
        rc_fail = cli_client.main(
            ["batch", "--name", "日常全清", "--root", str(tmp_path), "--on-busy", "fail", "--json"]
        )
        assert rc_fail == cli_client.EXIT_BUSY
        busy_out = json.loads(capsys.readouterr().out)
        assert busy_out["code"] == cli_client.EXIT_BUSY

        # 6. --on-busy wait 在 CLI 侧轮询重试，首任务结束后立即接棒成功
        orig_interval = cli_client.BUSY_RETRY_INTERVAL
        cli_client.BUSY_RETRY_INTERVAL = 0.05
        try:
            wait_rc: list[int] = []
            t_wait = threading.Thread(
                target=lambda: wait_rc.append(
                    cli_client.main([
                        "batch", "--name", "日常全清",
                        "--root", str(tmp_path),
                        "--on-busy", "wait",
                        "--json",
                    ])
                ),
                daemon=True,
            )
            t_wait.start()
            time.sleep(0.15)
            gate.set()
            t_first.join(timeout=5.0)
            t_wait.join(timeout=5.0)
            assert wait_rc == [cli_client.EXIT_OK]
            out_wait = json.loads(capsys.readouterr().out)
            assert out_wait["ok"] is True
            assert out_wait["ran"] == "日常全清"
        finally:
            cli_client.BUSY_RETRY_INTERVAL = orig_interval
    finally:
        srv.stop()


def test_control_server_stop_and_idle_exit(tmp_path: Path, capsys):
    """验证优雅中止（stop）与无头空闲自动退出（idle_exit）。"""
    ctrl_path = tmp_path / "config" / "session" / "control.json"
    started = threading.Event()

    def cancellable_runner(_payload, stop_event, emit):
        started.set()
        while not stop_event.wait(0.05):
            pass
        emit("log", {"level": "WARNING", "message": "cancelled"})
        return {"ok": False, "code": cli_client.EXIT_INTERRUPTED, "error": "运行已中断"}

    srv = ControlServer(
        mode="headless",
        control_path=ctrl_path,
        instance_id="idle-inst",
        idle_exit=0.35,
        job_runner=cancellable_runner,
    )
    srv.start()
    try:
        res_code: list[int] = []
        t = threading.Thread(
            target=lambda: res_code.append(
                cli_client.main(["--exec", "--id", "long_task", "--root", str(tmp_path), "--quiet"])
            ),
            daemon=True,
        )
        t.start()
        assert started.wait(timeout=3.0)

        # status 报告繁忙
        assert cli_client.main(["status", "--root", str(tmp_path), "--json"]) == 0
        st = json.loads(capsys.readouterr().out)
        assert st["running"] is True
        assert st["busy"] is True

        # stop 中止任务
        assert cli_client.main(["stop", "--root", str(tmp_path), "--json"]) == 0
        stop_out = json.loads(capsys.readouterr().out)
        assert stop_out["cancelled"] is True
        t.join(timeout=5.0)
        assert res_code == [cli_client.EXIT_INTERRUPTED]

        # 任务结束后等待空闲超时触发 shutdown_event
        assert srv.shutdown_event.wait(timeout=2.0)
    finally:
        srv.stop()


def test_stale_control_file_triggers_headless_autostart(tmp_path: Path, monkeypatch):
    """残留的僵死 control.json 探活失败时自动拉起无头实例。"""
    ctrl_path = tmp_path / "config" / "session" / "control.json"
    write_control_file(
        ControlEndpoint(
            pid=999999,
            instance_id="dead-inst",
            port=1,
            token="stale",
            mode="headless",
            started_at="2026-11-26T00:00:00Z",
        ),
        ctrl_path,
    )
    spawned: list[tuple[Path, float]] = []
    srv = ControlServer(mode="headless", control_path=ctrl_path, instance_id="fresh-inst")

    def fake_spawn(root: Path, *, idle_exit: float) -> None:
        spawned.append((root, idle_exit))
        srv.start()

    monkeypatch.setattr(cli_client, "_spawn_headless_server", fake_spawn)
    try:
        ctrl = cli_client._ensure_control(
            tmp_path, autostart=True, idle_exit=15.0, start_timeout=5.0, quiet=True,
        )
        assert ctrl is not None
        assert ctrl["instance_id"] == "fresh-inst"
        assert spawned == [(tmp_path, 15.0)]
    finally:
        srv.stop()


def test_short_param_flag_idle_exit_zero_and_job_retention_cap(tmp_path: Path, capsys):
    """验证 -p 短参、idle_exit=0 首单跑完即退与已完成作业字典上限裁剪。"""
    ctrl_path = tmp_path / "config" / "session" / "control.json"
    received: list[dict[str, Any]] = []

    def quick_runner(payload, _stop_event, _emit):
        received.append(dict(payload))
        return {"ok": True, "code": 0}

    # 1. 验证 _jobs 上限裁剪（最多保留 20 条已完成作业）
    srv = ControlServer(
        mode="headless",
        control_path=ctrl_path,
        instance_id="cap-inst",
        idle_exit=-1.0,
        job_runner=quick_runner,
    )
    srv.start()
    try:
        for i in range(25):
            rc = cli_client.main([
                "run", "--id", f"t_{i}", "-p", f"k={i}",
                "--root", str(tmp_path), "--quiet",
            ])
            assert rc == 0
        assert received[0]["params"] == {"k": "0"}
        assert len(srv._jobs) <= 20
    finally:
        srv.stop()

    # 2. 验证 idle_exit=0 在首单跑完后立刻触发 shutdown_event
    srv_once = ControlServer(
        mode="headless",
        control_path=ctrl_path,
        instance_id="once-inst",
        idle_exit=0.0,
        job_runner=quick_runner,
    )
    srv_once.start()
    try:
        rc = cli_client.main(["run", "--id", "once_task", "--root", str(tmp_path), "--quiet"])
        assert rc == 0
        assert srv_once.shutdown_event.wait(timeout=1.5)
    finally:
        srv_once.stop()


def test_gui_other_task_running_label_sees_active_control_job(tmp_path: Path):
    """控制面运行 CLI 任务时，GUI 侧 other_task_running_label 能感知占用防止并发冲突。"""
    from types import SimpleNamespace

    from lvjiang.core.control import start_control_server, stop_control_server
    from lvjiang.ui.main.run_worker import other_task_running_label

    ctrl_path = tmp_path / "config" / "session" / "control.json"
    started = threading.Event()
    gate = threading.Event()

    def blocking_runner(_payload, _stop_event, _emit):
        started.set()
        gate.wait(timeout=5.0)
        return {"ok": True, "code": 0}

    start_control_server(
        mode="gui",
        control_path=ctrl_path,
        instance_id="gui-inst",
        idle_exit=-1.0,
        job_runner=blocking_runner,
    )
    try:
        host = SimpleNamespace(_current_run_context=None)
        assert other_task_running_label(host, "daily") == ""

        t = threading.Thread(
            target=lambda: cli_client.main(["run", "--id", "bg", "--root", str(tmp_path), "--quiet"]),
            daemon=True,
        )
        t.start()
        assert started.wait(timeout=3.0)
        assert other_task_running_label(host, "daily") != ""

        gate.set()
        t.join(timeout=5.0)
        assert other_task_running_label(host, "daily") == ""
    finally:
        stop_control_server()
