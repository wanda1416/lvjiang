"""材料分类器验证脚本

对 data/materials 下每张参考图执行两轮测试：
1. OCR 文字识别：对整图做 OCR，输出识别到的全部文字
2. 材料识别：模板匹配类型 + OCR 等级/数量，输出完整识别结果

用法: python scripts/test_material_recognizer.py
"""

import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.core.ocr import OCREngine
from lvjiang.core.material_recognizer import MaterialRecognizer


def load_img(path: Path) -> np.ndarray:
    """PIL 读图（支持中文路径）-> BGR numpy"""
    rgb = np.array(Image.open(path))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def main():
    templates_dir = ROOT / "data" / "materials"
    if not templates_dir.exists():
        print(f"参考图目录不存在: {templates_dir}")
        sys.exit(1)

    ocr = OCREngine()
    recognizer = MaterialRecognizer(ocr, templates_dir)

    # 打印参考库
    types = recognizer.list_types()
    print(f"参考库: {len(types)} 种类型")
    for t in types:
        print(f"  - {t} ({recognizer.get_reference_count(t)} 张)")
    print()

    images = sorted(templates_dir.glob("*.png"))

    # ── 第一轮：OCR 文字识别 ──────────────────────────────
    print("=" * 60)
    print("  第一轮：OCR 文字识别（整图 OCR）")
    print("=" * 60)
    for path in images:
        img = load_img(path)
        results = ocr.recognize(img)
        texts = [f"{r.text}({r.confidence:.2f})" for r in results]
        text_str = ", ".join(texts) if texts else "(无文字)"
        print(f"  {path.name:30s} -> {text_str}")
    print()

    # ── 第二轮：材料识别 ──────────────────────────────────
    print("=" * 60)
    print("  第二轮：材料识别（模板匹配 + 等级/数量 OCR）")
    print("=" * 60)
    total = 0
    passed = 0
    failed = 0

    for path in images:
        m = re.match(r"^(.+?)(?:_(\d+)级)?\.png$", path.name)
        if not m:
            continue
        expected_type = m.group(1)
        expected_level = int(m.group(2)) if m.group(2) else None

        img = load_img(path)
        result = recognizer.recognize(img)
        total += 1

        type_ok = result.type == expected_type
        if type_ok:
            passed += 1
        else:
            failed += 1

        status = "OK" if type_ok else "FAIL"
        count_str = str(result.count) if result.count is not None else '?'
        owned_str = str(result.owned) if result.owned is not None else '?'
        print(
            f"  [{status}] {path.name:30s}\n"
            f"         type={result.type} (expect={expected_type})  "
            f"level={result.level}  count={count_str}/{owned_str}  "
            f"conf={result.confidence:.3f}"
        )

    print()
    print(f"材料识别结果: {passed}/{total} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
