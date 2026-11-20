#!/usr/bin/env python3
"""一次性重标定：主页（game_main_page）图标区域的框尺寸与对齐。

依据（2026-11 实测，见提交说明）：把模板 PNG 与两端真实截图做 1:1 模板匹配，
命中位置与配置矩形一致（score 1.000）说明"框=裁图"，因此这里只做**框的统一**：

- 同一行的按钮统一顶边与尺寸（桌面顶右一排原本把「新」角标算进了框，
  顶边差 3.3px、高差 3.6px）；
- 同一列的按钮统一宽度/高度（桌面左侧 41.9/42.7/39.8、安卓中右 56.6/51.7）；
- 统一时**保持各框原有中心**，避免把 OCR 窗口/点击落点挪离真实图标；
- 纵向顶边保留实测值（那是图标真实上沿，差异来自裁图留白，不是位置错）。

幂等：重复执行结果一致。用法：
    python scripts/one_off/recalibrate_game_main_page.py [--check]

`--check` 只报告将发生的改动，不写盘。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

#: 端 → (布局文件, 画布像素宽高)
LAYOUTS = {
    "desktop": (_REPO_ROOT / "config/system/layouts/desktop/general/game_main_page.json", 1920, 1080),
    "android": (_REPO_ROOT / "config/system/layouts/android/general/game_main_page.json", 2800, 1260),
}

#: 端 → 区域 key → (目标框宽 px, 目标框高 px, 目标顶边 px | None=保留实测)
TARGETS: dict[str, dict[str, tuple[float, float, float | None]]] = {
    # 顶右一排：统一 63×63、顶边 6.8px；横向按各自中心重算（活动与菜单之间
    # 那个未定义的图标使节距不等，位置以实测为准，不能强行等距）
    "desktop": {
        "menu": (63, 63, 6.8),
        "huodong": (63, 63, 6.8),
        "wulinlu": (63, 63, 6.8),
        "shop": (63, 63, 6.8),
        "zhanling": (63, 63, 6.8),
        "heming": (63, 63, 6.8),
        # 左侧竖排：统一 40×42，顶边保留实测
        "zudui": (40, 42, None),
        "dongzuo": (40, 42, None),
        "liaotian": (40, 42, None),
    },
    # 安卓：中右两按钮统一 52×52；左下三按钮统一 53×53；顶边保留实测
    "android": {
        "dongzuo": (52, 52, None),
        "liaotian": (52, 52, None),
        "huanma": (53, 53, None),
        "tingfeng": (53, 53, None),
        "more_func": (53, 53, None),
    },
}


def _plan(env: str, doc: dict, width: int, height: int) -> list[str]:
    changes: list[str] = []
    regions = {r["key"]: r for r in doc["regions"] if "x_ratio" in r}
    for key, (want_w, want_h, want_top) in TARGETS[env].items():
        r = regions.get(key)
        if r is None:
            changes.append(f"  !! {key}: 布局里没有这个区域，跳过")
            continue
        cx = r["x_ratio"] + r["w_ratio"] / 2
        new_w = want_w / width
        new_h = want_h / height
        new_x = cx - new_w / 2
        if want_top is None:
            new_y = r["y_ratio"]
            top_note = "顶边保留"
        else:
            new_y = want_top / height
            top_note = f"顶边 {want_top}px"
        if (abs(new_x - r["x_ratio"]) < 1e-9 and abs(new_y - r["y_ratio"]) < 1e-9
                and abs(new_w - r["w_ratio"]) < 1e-9 and abs(new_h - r["h_ratio"]) < 1e-9):
            continue
        old_px = (r["x_ratio"]*width, r["y_ratio"]*height, r["w_ratio"]*width, r["h_ratio"]*height)
        changes.append(
            f"  {key:<10} {old_px[2]:.1f}x{old_px[3]:.1f}@{old_px[0]:.1f},{old_px[1]:.1f}"
            f"  ->  {want_w}x{want_h}@{new_x*width:.1f},{new_y*height:.1f}  ({top_note})")
        r["x_ratio"], r["y_ratio"] = new_x, new_y
        r["w_ratio"], r["h_ratio"] = new_w, new_h
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    args = ap.parse_args()
    for env, (path, width, height) in LAYOUTS.items():
        doc = json.loads(path.read_text(encoding="utf-8"))
        print(f"== {env}: {path.relative_to(_REPO_ROOT).as_posix()} (canvas {width}x{height})")
        changes = _plan(env, doc, width, height)
        if not changes:
            print("   无需改动")
            continue
        for line in changes:
            print(line)
        if args.check:
            continue
        doc["content_version"] = int(doc.get("content_version", 0)) + 1
        # 保持原格式：两空格缩进、中文不转义、末尾无换行（与仓库现状一致）
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"   已写盘，content_version -> {doc['content_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
