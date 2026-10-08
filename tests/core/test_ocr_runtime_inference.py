"""随包模型必须能实际推理，防止依赖升级只通过替身测试却破坏 OCR。"""

import cv2
import numpy as np

from lvjiang.core.ocr import OCREngine


def test_shipped_ocr_models_recognize_digits():
    image = np.full((100, 600, 3), 255, dtype=np.uint8)
    cv2.putText(image, "LVJIANG 12345", (10, 65), cv2.FONT_HERSHEY_SIMPLEX,
                1.5, (0, 0, 0), 3)
    engine = OCREngine()
    try:
        results = engine.recognize(image)
        assert "12345" in " ".join(result.text for result in results)
    finally:
        engine.close()
