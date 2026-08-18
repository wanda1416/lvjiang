"""背包物品识别回归测试

使用真实截图验证 MaterialRecognizer 对背包格子的识别准确率。
验证内容：材料名称 + 等级（忽略数量）。

截图来源：config/session/screenshots/默认布局/bag_item_detail__bag_detail.png
标准结果：人工标注的 30 个格子（5 行 × 6 列）

注意：依赖 RapidOCR（ONNX Runtime），CI 环境可能不支持，仅本地运行。
"""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from lvjiang.apps.yysls.core.recognizer.material_recognizer import MaterialRecognizer
from lvjiang.core.ocr import OCREngine

# CI 环境（GitHub Actions 等）可能不支持 ONNX Runtime，跳过整组测试
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="背包识别测试依赖 RapidOCR，CI 环境不支持 ONNX Runtime",
)

# ─── 测试数据 ──────────────────────────────────────────────

# 截图路径
SCREENSHOT_PATH = Path(__file__).parent / "data" / "bag_item_detail__bag_detail.png"

# 标准结果：{(row, col): (material_name, level)}
# row: 1-5, col: 1-6
# level: None 表示无等级标识
EXPECTED_RESULTS = {
    # 第 1 行
    (1, 1): ("大律准石", 86),
    (1, 2): ("大律准石", 81),
    (1, 3): ("大律准石", 71),
    (1, 4): ("大律准石", 61),
    (1, 5): ("大律准石", 56),
    (1, 6): ("大律准石", 51),
    # 第 2 行
    (2, 1): ("大律准石", None),
    (2, 2): ("溯玉", None),
    (2, 3): ("律准石", None),
    (2, 4): ("振玉", None),
    (2, 5): ("小律准石", 96),
    (2, 6): ("小律准石", 71),
    # 第 3 行
    (3, 1): ("小律准石", None),
    (3, 2): ("小律准石", 56),
    (3, 3): ("小律准石", 51),
    (3, 4): ("彩狗粮", 110),
    (3, 5): ("彩狗粮", 105),
    (3, 6): ("金狗粮", 105),
    # 第 4 行
    (4, 1): ("彩狗粮", 100),
    (4, 2): ("金狗粮", 100),
    (4, 3): ("彩狗粮", 96),
    (4, 4): ("金狗粮", 96),
    (4, 5): ("彩狗粮", 91),
    (4, 6): ("金狗粮", 91),
    # 第 5 行
    (5, 1): ("彩狗粮", 86),
    (5, 2): ("彩狗粮", 81),
    (5, 3): ("金狗粮", 81),
    (5, 4): ("彩狗粮", 71),
    (5, 5): ("金狗粮", 71),
    (5, 6): ("金狗粮", 61),
}

# Panel key
BAG_PANEL_KEY = "bag_grid"

# 每个格子的匹配分组（默认"调律材料"，个别格子属于其他分组）
SLOT_GROUPS = {
    (2, 2): "装备培养",  # 溯玉
    (2, 4): "装备培养",  # 振玉
}
DEFAULT_GROUP = "调律材料"


# ─── Fixture ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def screenshot():
    """加载测试截图"""
    if not SCREENSHOT_PATH.exists():
        pytest.skip(f"截图不存在: {SCREENSHOT_PATH}")
    # cv2.imread 不支持中文路径，用 np.fromfile + imdecode
    data = SCREENSHOT_PATH.read_bytes()
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        pytest.skip(f"截图加载失败: {SCREENSHOT_PATH}")
    return img


@pytest.fixture(scope="module")
def layout():
    """加载默认布局的 bag_item_detail 场景区域"""
    from lvjiang.core.layout_manager import load_layout_by_name
    layout = load_layout_by_name("默认布局")
    if layout is None:
        pytest.skip("默认布局不存在")
    return layout


@pytest.fixture(scope="module")
def recognizer():
    """创建材料识别器"""
    ocr = OCREngine()
    return MaterialRecognizer(ocr)


def crop_bag_cell(img: np.ndarray, cell_bounds: tuple[int, int, int, int]) -> np.ndarray:
    """从截图中裁剪 panel grid 的单个 cell

    cell_bounds: (x1, y1, x2, y2) 像素坐标
    """
    x1, y1, x2, y2 = cell_bounds
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"裁剪为空: bounds={cell_bounds}")
    return crop


# ─── 测试用例 ──────────────────────────────────────────────

class TestBagItemRecognition:
    """背包物品识别测试"""

    def test_screenshot_exists(self, screenshot):
        """截图加载成功"""
        assert screenshot is not None
        assert screenshot.shape[0] > 0 and screenshot.shape[1] > 0

    def test_layout_has_bag_panel(self, layout):
        """布局包含背包网格 panel"""
        panels = layout.get_scene_panels("bag_item_detail")
        assert len(panels) >= 1, f"Panel 数量不足: {len(panels)}"
        # 检查 bag_grid panel 是否存在
        panel_keys = {p.key for p in panels}
        assert BAG_PANEL_KEY in panel_keys, f"缺少 panel: {BAG_PANEL_KEY}"

    def test_recognition_accuracy_all_slots(self, screenshot, layout, recognizer):
        """一次性验证全部 30 格的材料名称和等级识别准确率

        合并原 30 个参数化测试 + 汇总测试为单一用例，
        避免同一张截图重复校准 panel 和创建 OCREngine。
        """
        from lvjiang.core.ocr import OCREngine

        # 获取 panel
        panels = layout.get_scene_panels("bag_item_detail")
        panel = next((p for p in panels if p.key == BAG_PANEL_KEY), None)
        assert panel is not None, f"Panel 不存在: {BAG_PANEL_KEY}"

        # 使用 OCREngine 校准 panel（只需一次）
        ocr = OCREngine()
        canvas = layout.get_canvas()
        cells = ocr.calibrate_panel_cells(screenshot, canvas, panel)
        assert cells, "Panel 校准失败，无有效 cell"

        errors = []

        for (row, col), (expected_name, expected_level) in EXPECTED_RESULTS.items():
            cell_idx = (row - 1) * panel.cols + (col - 1)
            if cell_idx >= len(cells):
                errors.append(f"背包格{row}_{col}: Cell 索引越界")
                continue
            cell_bounds = cells[cell_idx]
            slot_img = crop_bag_cell(screenshot, cell_bounds)

            # 识别（按格子限定匹配分组，避免跨组误匹配）
            group = SLOT_GROUPS.get((row, col), DEFAULT_GROUP)
            result = recognizer.recognize(slot_img, group=group)

            # 验证材料名称
            if result.type != expected_name:
                errors.append(
                    f"背包格{row}_{col}: 材料名称不匹配 "
                    f"(期望={expected_name!r}, 实际={result.type!r}, "
                    f"置信度={result.confidence:.2f})"
                )
            # 验证等级（None 表示无等级标识，允许识别为 None）
            elif expected_level is not None and result.real_level != expected_level:
                errors.append(
                    f"背包格{row}_{col}: 等级不匹配 "
                    f"(期望={expected_level}, 实际={result.real_level})"
                )

        if errors:
            print("\n识别错误详情:")
            for e in errors:
                print(f"  {e}")

        assert not errors, f"{len(errors)} 个格子识别失败:\n" + "\n".join(errors)
