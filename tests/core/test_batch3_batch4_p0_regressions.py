"""批次 3 & 批次 4 P0 回归门禁：覆盖快照/仿真/OCR/识别与 Android 桥接核心修复。"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.android.recovery import AndroidRecovery
from lvjiang.core.android.scrcpy_capture import (
    FAULT_SOCKET_EOF,
    AndroidStreamCapture,
)
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.input.capture_base import capture_for_run, ensure_capture_channel
from lvjiang.core.ocr.cleaner import OCRCleaner
from lvjiang.core.ocr.config import (
    PcOCRConfig,
    load_ocr_config,
    save_recognition_settings,
)
from lvjiang.core.ocr.engine import OCREngine, clip_roi
from lvjiang.core.simulation.runner import _preflight
from lvjiang.core.simulation.scenario import Scenario, SimulatedDevice
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    TargetHandle,
    _ResourceProxy,
)
from lvjiang.workflows.capture_snapshot import CaptureSnapshotMixin


class _DummySnapshotHost(CaptureSnapshotMixin):
    def __init__(self, frames: list[np.ndarray | None]):
        self._init_capture_snapshot()
        self._frames = list(frames)
        self._capture = SimpleNamespace(
            capture=self._pop,
            get_capture_size=lambda: (100, 100),
        )

    def _pop(self) -> np.ndarray | None:
        return self._frames.pop(0) if self._frames else None


def test_capture_snapshot_readonly_and_no_stale_resurrection():
    """存入快照的帧必须只读，且内层清空快照后外层 capture_source 退出不得复活脏帧。"""
    f1 = np.zeros((16, 16, 3), dtype=np.uint8)
    f2 = np.ones((16, 16, 3), dtype=np.uint8)
    host = _DummySnapshotHost([f1, f2])

    got = host.capture_frame(source="init")
    assert got is not None

    with host.capture_source(from_last=True):
        assert host._reused_capture_frame is got
        with host.capture_source(from_last=True):
            host.clear_capture_snapshot()
        assert host._reused_capture_frame is None
    assert host._reused_capture_frame is None


def test_simulation_click_hold_advances_in_source_state_before_transition(tmp_path: Path):
    """带 hold 的点击应先在源状态推进持续时间，再完成跃迁或被源状态 timer 抢先打断。"""
    import hashlib
    import json

    import cv2

    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    ok, png = cv2.imencode(".png", frame)
    assert ok
    png_bytes = png.tobytes()
    (tmp_path / "f.png").write_bytes(png_bytes)
    png_sha = hashlib.sha256(png_bytes).hexdigest()
    wf_path = tmp_path / "a.wf"
    wf_path.write_text('log "ok"\n', encoding="utf-8", newline="\n")
    wf_sha = hashlib.sha256(wf_path.read_bytes()).hexdigest()

    spec = {
        "format_version": 1,
        "provenance": "synthetic",
        "run_env": "desktop",
        "workflow": "a.wf",
        "workflow_sha256": wf_sha,
        "start": "s0",
        "terminal": ["s1", "timeout"],
        "states": {
            "s0": {"frame": "f.png", "sha256": png_sha},
            "s1": {"frame": "f.png", "sha256": png_sha},
            "timeout": {"frame": "f.png", "sha256": png_sha},
        },
        "edges": [
            {"source": "s0", "target": "timeout", "action": "timer", "after": 1.0},
            {
                "source": "s0",
                "target": "s1",
                "action": "click",
                "match": {"box": [0, 0, 20, 20], "button": "left", "hold": [0.1, 3.0]},
            },
        ],
    }
    (tmp_path / "scenario.json").write_text(
        json.dumps(spec), encoding="utf-8", newline="\n"
    )
    scenario = Scenario.load(tmp_path)

    # hold=0.5 < timer(1.0)：正常跃迁到 s1，且 s1.entered == 0.5
    dev_ok = SimulatedDevice(scenario)
    dev_ok.click_screen(5, 5, hold=0.5)
    assert dev_ok.state == "s1"
    assert dev_ok.entered == pytest.approx(0.5)

    # hold=1.5 > timer(1.0)：源状态 timer 先触发切到 timeout，不得被旧 click 边覆盖
    dev_timeout = SimulatedDevice(scenario)
    dev_timeout.click_screen(5, 5, hold=1.5)
    assert dev_timeout.state == "timeout"


def test_simulation_preflight_prunes_dead_env_branch(tmp_path: Path):
    """_preflight 传入 run_env='desktop' 时应裁剪 if env('android') 死分支。"""
    wf = tmp_path / "cross_platform.wf"
    wf.write_text(
        'if env("android")\n'
        '    $x = unknown_android_only_fn()\n'
        'else\n'
        '    log "desktop ok"\n'
        'end\n',
        encoding="utf-8",
        newline="\n",
    )
    _preflight(wf, run_env="desktop", require_environment=True)
    with pytest.raises(Exception, match="unsupported_capability"):
        _preflight(wf, run_env="android", require_environment=True)


def test_clip_roi_clamps_negative_and_oob_coords():
    """负坐标与越界坐标必须被钳位到图像边界，杜绝 NumPy 负索引回绕。"""
    assert clip_roi(-10, -5, 20, 30, 100, 80) == (0, 0, 20, 30)
    assert clip_roi(90, 70, 150, 120, 100, 80) == (90, 70, 100, 80)
    assert clip_roi(-50, -50, -10, -10, 100, 80) == (0, 0, 0, 0)
    assert clip_roi(10, 10, 5, 5, 100, 80) == (10, 10, 10, 10)


def test_ocr_engine_lock_prevents_concurrent_close_uaf():
    """推理期间并发调用 close() 不得把正在使用的 backend 置空导致 TypeError。"""
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class _SlowBackend:
        def __call__(self, img):
            entered.set()
            release.wait(timeout=2.0)
            return [[[[0, 0], [10, 0], [10, 10], [0, 10]], "ok", 0.99]], None

        def close(self):
            closed.set()

    engine = OCREngine(backend_factory=_SlowBackend)
    errors: list[Exception] = []
    results: list = []

    def run_ocr():
        try:
            results.append(engine.recognize(np.zeros((16, 16, 3), dtype=np.uint8)))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=run_ocr)
    t1.start()
    assert entered.wait(timeout=2.0)

    t2 = threading.Thread(target=engine.close)
    t2.start()
    time.sleep(0.05)
    # close() 必须等推理释放锁后才真正关闭后端
    assert not closed.is_set()
    release.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not errors
    assert len(results) == 1 and results[0][0].text == "ok"
    assert closed.is_set()


def test_ocr_cleaner_save_preserves_recognition_settings(tmp_path: Path, monkeypatch):
    """OCRCleaner 落盘时必须重读 ocr.yaml 仅合并 normalization，不得覆盖 recognition 设置。"""
    resolver = ConfigResolver(tmp_path, dev_mode=False)
    resolver.system_dir.mkdir(parents=True, exist_ok=True)
    resolver.local_dir.mkdir(parents=True, exist_ok=True)
    (resolver.system_dir / "ocr.yaml").write_text(
        "recognition:\n"
        "  pc_ocr:\n"
        "    threads: 2\n"
        "    inter_threads: 1\n"
        "normalization:\n"
        "  default:\n"
        "    replacements: {}\n"
        "    patterns: {}\n"
        "  groups: {}\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr("lvjiang.core.config.get_resolver", lambda: resolver)
    OCRCleaner.reset_instance()
    try:
        cleaner = OCRCleaner()
        # 模拟 UI 先更新了 recognition.pc_ocr.threads = 4
        save_recognition_settings(pc_ocr=PcOCRConfig(threads=4, inter_threads=2))
        # 再由旧实例 OCRCleaner 保存清洗规则
        cleaner.set_replacements({"错": "对"})

        merged = load_ocr_config()
        assert merged["recognition"]["pc_ocr"]["threads"] == 4
        assert merged["recognition"]["pc_ocr"]["inter_threads"] == 2
        assert merged["normalization"]["default"]["replacements"] == {"错": "对"}
    finally:
        OCRCleaner.reset_instance()


def test_scrcpy_fault_clears_frame_proxy_routes_blocking_and_stop_survives_pkill_timeout(monkeypatch):
    """真断流清空旧帧、_ResourceProxy 正确走 capture_blocking/健康闸门，且 stop() 遇 pkill 超时不得抛错。"""
    dev = MagicMock()
    dev._base.return_value = ["adb", "-s", "fake"]
    stream = AndroidStreamCapture(dev)
    stream._running = True
    stream._latest_frame = np.ones((8, 8, 3), dtype=np.uint8)
    stream._ready_event.set()

    # 1. _declare_fault(FAULT_SOCKET_EOF) 登记故障标记
    stream._declare_fault(FAULT_SOCKET_EOF, "eof")
    assert stream.last_fault == FAULT_SOCKET_EOF

    # 2. _ResourceProxy 包装后仍必须命中 ensure_channel_healthy 与 capture_blocking
    handle = TargetHandle(
        ExecutionTarget(
            id="t1",
            kind="adb",
            display_name="test",
            capture=stream,
            device=dev,
        )
    )
    proxy = _ResourceProxy(handle, "capture")

    called = {"healthy": 0, "blocking": 0}
    monkeypatch.setattr(stream, "ensure_channel_healthy", lambda: called.__setitem__("healthy", called["healthy"] + 1))
    monkeypatch.setattr(stream, "capture_blocking", lambda timeout=5.0: (called.__setitem__("blocking", called["blocking"] + 1), np.zeros((4, 4, 3), dtype=np.uint8))[1])

    ensure_capture_channel(proxy)
    frame = capture_for_run(proxy)
    assert called == {"healthy": 1, "blocking": 1}
    assert frame is not None

    # 3. stop() 遇 pkill TimeoutExpired 不得抛错，且 _decoder 仍被释放
    stream._scid = 0x12345678
    stream._decoder = MagicMock()
    monkeypatch.setattr(
        "lvjiang.core.android.scrcpy_capture.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("adb", 5)),
    )
    stream.stop()
    assert stream._decoder is None


def test_recover_stream_survives_restart_error():
    """recover_stream 遇 restart 异常不得打穿恢复状态机。"""
    dev = MagicMock()
    dev.recovery = SimpleNamespace(stream_attempts=2, retry_interval=0.0)
    dev.stop_check = lambda: False
    dev.on_connection_lost = None
    dev.on_connection_restored = None
    rec = AndroidRecovery(dev)

    calls = {"n": 0}

    class _FlakyStream:
        last_fault_detail = "boom"

        def restart(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("temporary adb error")
            return True

        def clear_fault(self):
            pass

    rec.recover_stream(_FlakyStream())
    assert calls["n"] == 2
