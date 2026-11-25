#!/usr/bin/env python3
"""生成桌面端应用图标（与手机端 ``ic_launcher.xml`` 同源）。

产物（相对仓库根）：

- ``src/lvjiang/assets/icon.png``  — 256×256，Qt 运行时加载的窗口/应用图标；
- ``src/lvjiang/assets/icon.ico``  — 16/24/32/48/64/128/256 多尺寸，给 exe 与安装包用。

配色：形状一律相同；手机端是主色圆底（#006B5D）+ 白色音叉，桌面端把圆底换成
浅一档的绿（#07C160，微信绿），音叉仍为白色。

图形与 ``android/app/src/main/res/drawable/ic_launcher.xml`` 逐项对应：主色圆底
（#006B5D）＋白色音叉笔画（stroke 2.5/48、圆头圆角）。手机端图标是矢量、桌面端
需要位图，所以用同一组几何参数重画，而不是另做一套视觉。

改图标时改这里再重跑：``python scripts/one_off/make_app_icon.py``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "src" / "lvjiang" / "assets"

#: 几何参数与手机端 ``ic_launcher.xml`` 完全一致（改动必须两边同步）
VIEWPORT = 48.0
STROKE = 2.5
PNG_SIZE = 256
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: 手机端：主色圆底 + 白色音叉
ANDROID_BACKGROUND = "#006B5D"
ANDROID_GLYPH = "#FFFFFF"

#: 桌面端：同形同结构，只把圆底换成浅一档的绿（微信绿），音叉仍是白色。
#: 想换成别的浅绿只改这一个常量，重跑本脚本即可（如 #2FA37B 更接近原色相）。
DESKTOP_BACKGROUND = "#07C160"
DESKTOP_GLYPH = "#FFFFFF"


def render(size: int, background: str = DESKTOP_BACKGROUND,
           glyph: str = DESKTOP_GLYPH):
    """按手机端矢量的几何参数画一张 ``size``×``size`` 的图标。

    形状逐项对应 ``ic_launcher.xml``：圆底半径 22；音叉两管 x=18/30 从 y=14
    到 y=25，底部由 r=6 的半圆连起来（SVG ``A6,6 0 0 0 30,25``，sweep=0 表示
    经**下方**到终点，所以是标准音叉的 U 而非拱形），柄 y=31→37，底座 y=37。
    """
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    scale = size / VIEWPORT
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 圆底：中心 (24,24)、半径 22（与 pathData 的 a22,22 一致）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawEllipse(
            QRectF((24 - 22) * scale, (24 - 22) * scale, 44 * scale, 44 * scale))

        # 音叉：两条竖管 + 底部半圆（经下方）+ 柄 + 底座横线
        path = QPainterPath()
        path.moveTo(18 * scale, 14 * scale)
        path.lineTo(18 * scale, 25 * scale)
        # Qt 的角度 0=3 点、90=12 点、270=6 点；180→360 才是「左侧经下方到右侧」，
        # 负向扫过 90 会从上方绕，画出来是拱形，与手机端不是同一个形状。
        path.arcTo(QRectF(18 * scale, 19 * scale, 12 * scale, 12 * scale), 180, 180)
        path.lineTo(30 * scale, 14 * scale)
        path.moveTo(24 * scale, 31 * scale)
        path.lineTo(24 * scale, 37 * scale)
        path.moveTo(20 * scale, 37 * scale)
        path.lineTo(28 * scale, 37 * scale)

        pen = QPen(QColor(glyph))
        pen.setWidthF(STROKE * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
    finally:
        painter.end()
    return image


def render_android(size: int):
    """手机端配色的同一枚图标，用于核对桌面版没有画走样。"""
    return render(size, background=ANDROID_BACKGROUND, glyph=ANDROID_GLYPH)


def main() -> int:
    from PIL import Image
    from PyQt6.QtGui import QGuiApplication

    QGuiApplication.instance() or QGuiApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)

    png_path = ASSETS / "icon.png"
    render(PNG_SIZE).save(str(png_path), "PNG")

    staging = ASSETS / "_icon-256.png"
    render(max(ICO_SIZES)).save(str(staging), "PNG")
    with Image.open(staging) as source:
        source.convert("RGBA").save(
            ASSETS / "icon.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    staging.unlink(missing_ok=True)

    print(f"已生成: {png_path.relative_to(ROOT)}, {(ASSETS / 'icon.ico').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
