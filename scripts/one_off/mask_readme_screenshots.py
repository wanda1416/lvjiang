"""对 README / 用户指南截图中的用户（角色名）与设备信息做像素化打码。

一次性脚本：docs/assets/screenshots/ 下的截图由本脚本从原始截图生成。
原始截图不放仓库；如需重新打码，把原始截图复制到 tmp_readme_shots/img{1..5}.png
后运行：

    python scripts/one_off/mask_readme_screenshots.py

坐标为对应截图的像素区域（打码对象：用户名、角色名、局域网 IP、设备型号）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "tmp_readme_shots"
OUT_DIR = REPO_ROOT / "docs" / "assets" / "screenshots"

# 源图 -> (输出文件名, 打码区域列表 [(left, top, right, bottom), ...])
MASKS: dict[int, tuple[str, list[tuple[int, int, int, int]]]] = {
    1: (
        "main-battle-prep.png",
        [
            (40, 88, 238, 116),  # 顶栏用户下拉框
            (24, 156, 172, 184),  # 连接栏 IP
            (1158, 202, 1400, 230),  # 连接下拉框 IP + 设备型号
            (432, 512, 605, 550),  # 当前属性方案名（含用户名前缀）
            (32, 636, 215, 664),  # 参数设置（用户独立）
            (455, 1525, 528, 1558),  # 底部日志角色名
        ],
    ),
    2: (
        "main-batch-overview.png",
        [
            (40, 88, 238, 116),  # 顶栏用户下拉框
            (24, 156, 172, 184),  # 连接栏 IP
            (1158, 202, 1400, 230),  # 连接下拉框 IP + 设备型号
            (26, 478, 92, 1552),  # 批量任务列表角色名列
            (455, 1525, 528, 1558),  # 底部日志角色名
        ],
    ),
    3: (
        "game-config.png",
        [
            (706, 300, 832, 518),  # 基础属性列表角色名前缀
        ],
    ),
    4: ("tuning-config.png", []),
    5: ("graduation-analysis.png", []),
}

PIXEL_FACTOR = 14  # 像素化强度：区域缩小为 1/14 再还原


def pixelate(im: Image.Image, box: tuple[int, int, int, int]) -> None:
    region = im.crop(box)
    width, height = region.size
    small = region.resize(
        (max(1, width // PIXEL_FACTOR), max(1, height // PIXEL_FACTOR)),
        Image.LANCZOS,
    )
    im.paste(small.resize((width, height), Image.NEAREST), box[:2])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for index, (out_name, boxes) in MASKS.items():
        src = SRC_DIR / f"img{index}.png"
        if not src.exists():
            print(f"跳过：缺少 {src}")
            continue
        im = Image.open(src).convert("RGB")
        for box in boxes:
            pixelate(im, box)
        out = OUT_DIR / out_name
        im.save(out)
        print(f"{src.name} -> {out.relative_to(REPO_ROOT)} ({len(boxes)} 处打码)")


if __name__ == "__main__":
    main()
