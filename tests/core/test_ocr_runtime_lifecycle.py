"""保护 OCR 后端边界、生命周期，不让真实故障变成空识别继续执行。"""
import json
import sys
from types import SimpleNamespace

import numpy as np

from lvjiang.core.ocr import OCREngine
from lvjiang.core.ocr_config import ocr_engine_options
from lvjiang.core.ondevice import offline, task_runner, workflow_runner


def test_android_uses_native_backend_without_changing_pc_defaults(monkeypatch):
    options = []
    device_configs = []

    def backend(**kwargs):
        options.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=backend))
    monkeypatch.setattr("lvjiang.core.ondevice.native_ocr.NativeOCRBackend",
                        lambda config: device_configs.append(config) or SimpleNamespace())
    assert OCREngine()._ensure_loaded()
    assert workflow_runner._create_ocr()._ensure_loaded()
    # PC 端按 ocr.yaml 的识别性能分组构造；手机内存预算不能串到 PC 侧，
    # 手机侧的线程数也仍由 NativeOCRBackend 自己那份配置决定。
    assert options == [ocr_engine_options()]
    assert "max_detector_pixels" not in options[0]
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

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=backend))
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


def test_uninitialized_apk_checks_dependencies_without_creating_engine(monkeypatch):
    monkeypatch.setattr(offline, "configuration_status", lambda: {"ready": False})
    monkeypatch.setattr(task_runner, "is_running", lambda: False)

    def not_allowed():
        raise AssertionError("预置配置未初始化时不应初始化引擎")

    monkeypatch.setattr(task_runner, "_get_engine", not_allowed)
    result = json.loads(offline.check_runtime())
    assert not result["ok"] and "预置配置尚未初始化" in result["message"]
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


def test_engine_rebuilds_when_recognition_options_change(monkeypatch):
    """识别配置改了就重建会话：否则用户点了保存却要重启才生效。"""
    calls = []

    def backend(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=backend))
    state = {"options": {"intra_op_num_threads": 2, "inter_op_num_threads": 1,
                         "det_limit_side_len": 736}}
    monkeypatch.setattr("lvjiang.core.ocr_config.ocr_engine_options",
                        lambda: dict(state["options"]))

    engine = OCREngine()
    assert engine._ensure_loaded()
    assert engine._ensure_loaded()          # 参数没变：复用同一个会话
    assert len(calls) == 1

    state["options"]["intra_op_num_threads"] = 4     # 用户在识别配置里改成 4
    assert engine._ensure_loaded()
    assert len(calls) == 2 and calls[1]["intra_op_num_threads"] == 4


def test_explicit_options_win_over_recognition_config(monkeypatch):
    """显式传参优先：测试与诊断代码可以钉死自己的参数。"""
    calls = []

    def backend(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=backend))
    assert OCREngine(rapidocr_options={"intra_op_num_threads": 1})._ensure_loaded()
    assert calls == [{"intra_op_num_threads": 1}]
