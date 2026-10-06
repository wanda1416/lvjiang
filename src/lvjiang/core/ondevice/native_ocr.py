"""完整原生 OCR 后端；Python 仅传字节图像并接收文字/四角坐标。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import yaml

from ..ocr_config import DeviceOCRConfig


class NativeOCRBackend:
    def __init__(self, config: DeviceOCRConfig):
        import rapidocr_onnxruntime
        from com.lvjiang.app import NativeOcrBridge

        root = Path(rapidocr_onnxruntime.__file__).parent
        settings = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        settings.update(threads=config.threads, max_detector_pixels=config.max_detector_pixels)
        paths = [str(root / settings[section]["model_path"]) for section in ("Det", "Cls", "Rec")]
        self._bridge = NativeOcrBridge(*paths, json.dumps(settings))
        self._closed = False

    def __call__(self, image: np.ndarray):
        if self._closed:
            raise RuntimeError("手机 OCR 会话已关闭")
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("手机原生 OCR 输入必须为 uint8 BGR 图像")
        if not image.size:
            return [], None
        from java.lang import OutOfMemoryError

        try:
            raw = self._bridge.recognize(image.tobytes(), int(image.shape[1]), int(image.shape[0]))
        except OutOfMemoryError as exc:
            raise MemoryError("手机 OCR 原生内存不足，已中止任务；请查看诊断日志") from exc
        return json.loads(str(raw)), None

    def close(self) -> None:
        if not self._closed:
            self._bridge.close()
            self._closed = True
