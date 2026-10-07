"""保护 OCR 后端边界、生命周期，不让真实故障变成空识别继续执行。"""
import json
from types import SimpleNamespace

import numpy as np

from lvjiang.core.ocr import OCREngine
from lvjiang.core.ondevice import offline, task_runner, workflow_runner


def test_android_uses_native_backend_without_changing_pc_defaults(monkeypatch):
    options = []
    device_configs = []

    def backend(**kwargs):
        options.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", backend)
    monkeypatch.setattr("lvjiang.core.ondevice.native_ocr.NativeOCRBackend",
                        lambda config: device_configs.append(config) or SimpleNamespace())
    assert OCREngine()._ensure_loaded()
    assert workflow_runner._create_ocr()._ensure_loaded()
    assert options == [{}]
    assert device_configs[0].threads == 2
    assert device_configs[0].max_detector_pixels == 1_200_000


def test_close_releases_all_sessions_once_and_allows_reinitialization(monkeypatch):
    closed = []
    backends = []

    def backend(**kwargs):
        value = SimpleNamespace(**{
            key: SimpleNamespace(session=SimpleNamespace(close=lambda key=key: closed.append(key)))
            for key in ("text_det", "text_cls", "text_rec")
        })
        backends.append(value)
        return value

    monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", backend)
    engine = OCREngine()
    assert engine._ensure_loaded()
    engine.close()
    engine.close()
    assert closed == ["text_det", "text_cls", "text_rec"]
    assert engine._ensure_loaded()
    assert engine._ocr is backends[1] and len(backends) == 2


def test_native_backend_fault_propagates_and_close_is_idempotent():
    import pytest

    closed = []

    class Backend:
        def __call__(self, image):
            raise RuntimeError("model output shape mismatch")

        def close(self):
            closed.append(True)

    engine = OCREngine(backend_factory=Backend)
    with pytest.raises(RuntimeError, match="shape mismatch"):
        engine.recognize(np.zeros((32, 32, 3), dtype=np.uint8))
    engine.close()
    engine.close()
    assert closed == [True]


def test_oom_propagates_instead_of_becoming_no_text():
    import pytest

    def exhausted(image):
        raise MemoryError("allocation failed")

    engine = OCREngine()
    engine._ocr = exhausted
    engine._available = True
    with pytest.raises(MemoryError, match="allocation failed"):
        engine.recognize(np.zeros((10, 10, 3), dtype=np.uint8))


def test_unsynced_apk_checks_dependencies_without_creating_engine(monkeypatch):
    monkeypatch.setattr(offline, "sync_status", lambda: {"synced": False})
    monkeypatch.setattr(task_runner, "is_running", lambda: False)

    def not_allowed():
        raise AssertionError("未同步时不应初始化引擎")

    monkeypatch.setattr(task_runner, "_get_engine", not_allowed)
    result = json.loads(offline.check_runtime())
    assert not result["ok"] and "PC" in result["message"]
    assert result["stages"] == ["运行依赖与 Profile 管线加载通过"]


def test_configuration_reload_releases_cached_ocr_before_discard(monkeypatch):
    released = []
    engine = SimpleNamespace(
        clear_capture_snapshot=lambda: released.append("frames"),
        _ocr=SimpleNamespace(close=lambda: released.append("models")),
    )
    monkeypatch.setattr(task_runner, "_ENGINE", engine)
    monkeypatch.setattr("lvjiang.core.ondevice.onnx_session.close_sessions", lambda: released.append("partial"))
    task_runner.release_engine()
    assert task_runner._ENGINE is None
    assert released == ["frames", "models", "partial"]


def test_task_oom_fails_and_releases_cached_models(monkeypatch, tmp_path):
    released = []

    def exhausted(*args, **kwargs):
        raise MemoryError("allocation failed")

    engine = SimpleNamespace(
        run_username="tester", users_dir=tmp_path / "users", execute=exhausted,
        clear_capture_snapshot=lambda: released.append("frames"),
        _ocr=SimpleNamespace(close=lambda: released.append("models")),
    )
    monkeypatch.setattr(task_runner, "_ENGINE", engine)
    monkeypatch.setattr(task_runner, "_STATE", task_runner._TaskState())
    monkeypatch.setattr(task_runner, "_reset_engine_state", lambda engine: None)
    monkeypatch.setattr(task_runner, "_build_source", lambda task, engine: "source")
    task_runner._STATE.begin("task", "task")
    task_runner._run_in_thread({"id": "task", "name": "task"}, None)

    state = task_runner._STATE.snapshot()
    assert state["state"] == "failed" and "内存不足" in state["message"]
    assert task_runner._ENGINE is None
    assert released == ["frames", "models"]
