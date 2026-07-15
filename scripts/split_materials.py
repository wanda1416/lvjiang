"""
将 data/screenshots 目录下的图片按 5行×6列 切分，
保存到 data/materials 目录。

用法: python scripts/split_materials.py
"""

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow: pip install Pillow")
    sys.exit(1)

# ── 配置 ──
ROWS = 5
COLS = 6
SRC_DIR = Path(__file__).resolve().parent.parent / "data" / "screenshots"
DST_DIR = Path(__file__).resolve().parent.parent / "data" / "materials"


def split_image(img_path: Path, dst: Path, prefix: str):
    """将单张图片切分为 ROWS×COLS 个小图并保存。"""
    img = Image.open(img_path)
    w, h = img.size
    cell_w = w // COLS
    cell_h = h // ROWS

    count = 0
    for row in range(ROWS):
        for col in range(COLS):
            left = col * cell_w
            upper = row * cell_h
            # 最后一列/行扩展到边缘，避免像素丢失
            right = w if col == COLS - 1 else (col + 1) * cell_w
            lower = h if row == ROWS - 1 else (row + 1) * cell_h

            cell = img.crop((left, upper, right, lower))
            name = f"{prefix}_r{row + 1}_c{col + 1}.png"
            cell.save(dst / name)
            count += 1

    return count


def main():
    if not SRC_DIR.exists():
        print(f"截图目录不存在: {SRC_DIR}")
        sys.exit(1)

    images = sorted(SRC_DIR.glob("*.png"))
    if not images:
        print(f"截图目录为空: {SRC_DIR}")
        sys.exit(1)

    DST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"源目录: {SRC_DIR}")
    print(f"输出目录: {DST_DIR}")
    print(f"切分规格: {ROWS}行 × {COLS}列")
    print(f"找到 {len(images)} 张图片")
    print()

    total = 0
    for img_path in images:
        prefix = img_path.stem  # e.g. "image1"
        count = split_image(img_path, DST_DIR, prefix)
        total += count
        print(f"  {img_path.name} -> {count} 张小图")

    print(f"\n完成! 共生成 {total} 张小图 -> {DST_DIR}")


if __name__ == "__main__":
    main()
