"""识别层区域校验单元测试

覆盖「点名的区域在当前布局未绑定坐标」这条路径：过去会被静默跳过，
by 子句因此退化成「未命中」、普通 OCR 悄悄少字段，现在一律抛错中断。
"""

import numpy as np
import pytest

from lvjiang.core.layout_models import CanvasConfig, Region
from lvjiang.workflows.base.recognition import _RecognitionMixin

SCENE = "activity_jianghu"


class _FakeLayout:
    """只提供识别层需要的两个查询方法"""

    def __init__(self, regions):
        self._regions = regions

    def get_canvas(self):
        return CanvasConfig()

    def get_scene_regions(self, scene_key):
        return list(self._regions) if scene_key == SCENE else []


class _FakeOCRResult:
    def __init__(self, text):
        self.text = text


class _FakeOCR:
    """按区域顺序返回预设文本；ocr_scene_regions 只返回 key 列表映射"""

    def __init__(self, texts=None):
        self.texts = texts or {}
        self.calls = []

    def recognize(self, crop):
        # 裁切图的首个像素值当区域序号用（见 _make_img）
        idx = int(crop[0][0][0])
        self.calls.append(idx)
        return [_FakeOCRResult(self.texts.get(idx, ""))]

    def ocr_scene_regions(self, img, canvas, regions, scene_key, min_confidence=None):
        return {r.key: self.texts.get(i, "") for i, r in enumerate(regions)}


class _FakeCapture:
    def __init__(self, img):
        self._img = img

    def capture(self):
        return self._img


class _Recognizer(_RecognitionMixin):
    """把 Mixin 拼成可独立实例化的最小对象"""

    def __init__(self, regions, texts=None):
        self._layout = _FakeLayout(regions)
        self._ocr = _FakeOCR(texts)
        self._capture = _FakeCapture(_make_img(len(regions)))


def _make_img(n_regions: int):
    """构造一张图：第 i 个区域的裁切块首像素值 = i，便于识别桩区分区域"""
    img = np.zeros((10 * max(n_regions, 1), 10, 3), dtype=np.uint8)
    for i in range(n_regions):
        img[i * 10:(i + 1) * 10, :, :] = i
    return img


def _regions(*keys):
    """按 keys 竖向切分画布，第 i 个区域正好落在 _make_img 的第 i 段"""
    n = len(keys)
    return [
        Region(key=k, x_ratio=0.0, y_ratio=i / n, w_ratio=1.0, h_ratio=1 / n)
        for i, k in enumerate(keys)
    ]


# ─── by 子句：未绑定的 key 必须报错，不能当成未命中 ─────────────

def test_ocr_scene_by_unbound_field_raises():
    rec = _Recognizer(_regions("label_0", "label_2"))
    with pytest.raises(ValueError, match="label_1"):
        rec.ocr_scene_by(SCENE, ["label_1"], ["合影", "换装"], "contains_any")


def test_ocr_scene_by_partial_unbound_raises():
    """混合场景：只要有一个 key 没绑定就报错，且不会先返回已命中的字段"""
    rec = _Recognizer(_regions("label_0"), texts={0: "合影"})
    with pytest.raises(ValueError, match="label_1"):
        rec.ocr_scene_by(SCENE, ["label_0", "label_1"], ["合影"], "contains_any")


def test_ocr_scene_by_hit_and_miss_still_work():
    rec = _Recognizer(_regions("label_0", "label_1"), texts={0: "东方", 1: "合影"})
    assert rec.ocr_scene_by(SCENE, ["label_0", "label_1"], "合影", "contains") == "label_1"
    assert rec.ocr_scene_by(SCENE, ["label_0", "label_1"], "醉意", "contains") == ""


def test_ocr_scene_by_without_fields_scans_whole_scene():
    """不点名字段时退化为整场景短路识别"""
    rec = _Recognizer(_regions("label_0", "label_1"), texts={1: "合影"})
    assert rec.ocr_scene_by(SCENE, [], ["合影"], "contains_any") == "label_1"


# ─── 普通 OCR / 材料识别：同样不容忍缺失 key ────────────────────

def test_ocr_scene_unbound_field_raises():
    rec = _Recognizer(_regions("label_0"))
    with pytest.raises(ValueError, match="label_1"):
        rec.ocr_scene(SCENE, ["label_0", "label_1"])


def test_ocr_scene_keeps_requested_order():
    rec = _Recognizer(_regions("label_0", "label_1"), texts={0: "东方", 1: "合影"})
    assert list(rec.ocr_scene(SCENE, ["label_1", "label_0"])) == ["label_1", "label_0"]


def test_ocr_scene_empty_scene_returns_empty():
    """场景整体没绑定且未点名字段时，保持原有的警告 + 空结果语义"""
    rec = _Recognizer([])
    assert rec.ocr_scene("not_bound_scene") == {}
