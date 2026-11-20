#!/usr/bin/env python3
"""一次性重标定（第二步）：直接用现有截图裁出主页图标模板，并让「框 = 模板 = 识别区域」。

依据：先把旧模板在当前截图里定位（1:1 匹配，score 1.000），以它的框中心为图标
中心，按统一尺寸重新裁一块——这样裁图与矩形同源同尺寸，留白一致，不会出现
「同一列但上下差 2~5px」。模板的 ``record_w/record_h`` 一律写成截图分辨率
（匹配器按 ``canvas_w / record_w`` 缩放，写错就等于把图放大/缩小后再比对）。

只处理确实需要重裁的四张（其余模板实测已是 1:1）：
    android ditu     68x68      （原 67x68，记录分辨率还是旧设备 2048x920）
    android dongzuo  52x57      （原 52x57，与 liaotian 统一成同尺寸）
    android liaotian 52x57      （原 52x52）
    desktop liaotian 40x42      （原 40x40，与左列兄弟统一）

幂等：先比对目标裁图与现有 PNG 是否逐字节相同，相同则跳过。用法：
    python scripts/one_off/recrop_main_page_icons.py [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

#: 端 → (布局文件, 截图, 画布在截图中的偏移, 画布像素宽高)
LAYOUTS = {
    "desktop": (
        _REPO_ROOT / "config/system/layouts/desktop/general/game_main_page.json",
        _REPO_ROOT / "config/session/screenshots/desktop/game_main_page.webp",
        (8, 31), (1920, 1080),
    ),
    "android": (
        _REPO_ROOT / "config/system/layouts/android/general/game_main_page.json",
        _REPO_ROOT / "config/session/screenshots/android/game_main_page.webp",
        (0, 0), (2800, 1260),
    ),
}

#: (端, key) → (目标宽 px, 目标高 px)；统一口径见模块说明
CROPS: dict[tuple[str, str], tuple[int, int]] = {
    ("android", "ditu"): (68, 68),
    ("android", "dongzuo"): (52, 57),
    ("android", "liaotian"): (52, 57),
    ("desktop", "liaotian"): (40, 42),
}

TEMPLATES = _REPO_ROOT / "config/system/templates"
MIN_SCORE = 0.8


def _canvas(env: str):
    _, shot, (ox, oy), (width, height) = LAYOUTS[env]
    img = cv2.imread(str(shot), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"读不到截图: {shot}")
    return img[oy:oy + height, ox:ox + width], width, height


def _locate(gray, name: str, rect_px, width: int):
    """用现有模板定位图标中心；没有模板时退回矩形中心"""
    path = TEMPLATES / f"{name}.png"
    tpl = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.exists() else None
    px, py, pw, ph = rect_px
    if tpl is None:
        return px + pw / 2, py + ph / 2, "矩形中心"
    th, tw = tpl.shape
    m = 80
    x1, y1 = max(0, int(px) - m), max(0, int(py) - m)
    x2, y2 = min(width, int(px + pw) + m), min(len(gray), int(py + ph) + m)
    res = cv2.matchTemplate(gray[y1:y2, x1:x2], tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < 0.7:
        return px + pw / 2, py + ph / 2, f"旧模板匹配过低({score:.3f})，退回矩形中心"
    return x1 + loc[0] + tw / 2, y1 + loc[1] + th / 2, f"旧模板 score={score:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    args = ap.parse_args()
    errors: list[str] = []
    for (env, key), (want_w, want_h) in CROPS.items():
        layout_path, _, _, (width, height) = LAYOUTS[env]
        canvas, width, height = _canvas(env)
        gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
        doc = json.loads(layout_path.read_text(encoding="utf-8"))
        region = next((r for r in doc["regions"]
                       if r.get("key") == key and "x_ratio" in r), None)
        if region is None:
            errors.append(f"{env}.{key}: 布局里没有该区域")
            continue
        rect_px = (region["x_ratio"] * width, region["y_ratio"] * height,
                   region["w_ratio"] * width, region["h_ratio"] * height)
        name = f"{env}/game_main_page/{key}"
        cx, cy, how = _locate(gray, name, rect_px, width)
        x, y = int(round(cx - want_w / 2)), int(round(cy - want_h / 2))
        x = max(0, min(x, width - want_w))
        y = max(0, min(y, height - want_h))
        patch = canvas[y:y + want_h, x:x + want_w]
        if patch.shape[0] != want_h or patch.shape[1] != want_w:
            errors.append(f"{env}.{key}: 裁窗越界 {patch.shape}")
            continue
        out = TEMPLATES / f"{name}.png"
        old = cv2.imread(str(out), cv2.IMREAD_COLOR) if out.exists() else None
        same_image = (old is not None and old.shape == patch.shape
                      and np.array_equal(old, patch))
        new_rect = (x / width, y / height, want_w / width, want_h / height)
        old_rect = (region["x_ratio"], region["y_ratio"],
                    region["w_ratio"], region["h_ratio"])
        rect_changed = any(abs(a - b) > 1e-9
                           for a, b in zip(new_rect, old_rect, strict=True))
        binding = {"name": name, "min_score": MIN_SCORE,
                   "record_w": width, "record_h": height}
        binding_changed = region.get("template") != binding
        print(f"{env}.{key:<9} {how}  裁窗=({x},{y},{want_w},{want_h})  "
              f"图{'未变' if same_image else '将更新'}"
              f"{'' if not rect_changed else '  框将调整'}"
              f"{'' if not binding_changed else '  绑定将更新'}")
        if args.check:
            continue
        if not (same_image and not rect_changed and not binding_changed):
            if not same_image:
                cv2.imwrite(str(out), patch)
            if rect_changed:
                region["x_ratio"], region["y_ratio"] = new_rect[0], new_rect[1]
                region["w_ratio"], region["h_ratio"] = new_rect[2], new_rect[3]
            if binding_changed:
                region["template"] = binding
            doc["content_version"] = int(doc.get("content_version", 0)) + 1
            layout_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                   encoding="utf-8", newline="\n")
            print(f"    已写盘：框=模板=({want_w}x{want_h}@{x},{y})，"
                  f"record={width}x{height}，content_version -> {doc['content_version']}")
        else:
            print("    已是目标状态，跳过")
    for err in errors:
        print("!!", err)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
