"""把微信 / 支付宝收款码原图裁成统一的 512 方形 PNG，并同步写入两处副本。

收款码原图是支付方生成的"分享卡片"（3:4 长图，上下大片品牌色），页面按
220px 宽显示时留给码面的只有约 90px，手机对着屏幕扫会明显吃力。这里只做
三件事：定位二维码、裁到"码 + 静默区"、统一重采样成正方形 PNG。不重编码
二维码内容、不加滤镜、不转灰度——裁切缩放之后必须还能解出原来的内容，
否则直接报错，宁可不出图也不产出扫不出来的码。

产物固定两处（同一张图的两个交付副本，改码时同一条命令重跑即可）：
    docs/assets/support/<渠道>.png            README 用（仓库内相对路径）
    server/web/public/support/<渠道>.png      站点用（lvjiang.wyxj.net/support/）

用法：
    python scripts/prepare_donate_qr.py --source "C:/Users/me/Pictures/收款码"
    python scripts/prepare_donate_qr.py --source ... --check   # 只校验副本是否一致
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

#: 输出边长。页面最大显示 240px，2x 屏需要 480，取 512 留余量。
OUTPUT_SIZE = 512

#: 静默区：QR 规范要求四周至少 4 个模块。检测框通常贴着码的外沿但略有余量，
#: 这里按"码面边长的 9%"再外扩一圈，对常见的 25~45 模块版本都够 4 个模块，
#: 又不会把白边做到喧宾夺主。
QUIET_ZONE_RATIO = 0.09

#: 输出 PNG 用调色板压缩：二维码本身就是黑白二值，唯一的高频内容是码中央的
#: 头像，16 色足够还原；不做这步时 512² 的 PNG 会到 100 KB 以上（JPEG 噪点
#: 让大面积白底无法压缩）。压完必须仍然解得出一模一样的内容才算数。
PALETTE_COLORS = 16

#: 渠道 → 原图文件名 / 输出文件名 / 简称（仅用于日志）。
CHANNELS = {
    "wechat": "wechat.jpg",
    "alipay": "alipay.jpg",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGETS = (
    PROJECT_ROOT / "docs" / "assets" / "support",
    PROJECT_ROOT / "server" / "web" / "public" / "support",
)


class QrPrepareError(RuntimeError):
    """原图无法定位、解码或校验失败。"""


def _decode(image: np.ndarray) -> str:
    """解出二维码内容；定位失败或内容为空都当成错误。"""
    detector = cv2.QRCodeDetector()
    text, points, _ = detector.detectAndDecode(image)
    if not text or points is None:
        raise QrPrepareError("没有在图中定位到可解码的二维码")
    return text


def _tight_box(image: np.ndarray, points: np.ndarray) -> tuple[float, float, float, float]:
    """把检测框收紧到实际的深色模块范围。

    检测器返回的四角往往带一圈余量，直接用会让静默区忽宽忽窄（还可能把
    卡片上的装饰线裁进来）。这里在检测框内找真正的深色像素范围，作为
    后续外扩静默区的基准。
    """
    xs = points[0][:, 0]
    ys = points[0][:, 1]
    height, width = image.shape[:2]
    left = max(int(xs.min()), 0)
    right = min(int(xs.max()) + 1, width)
    top = max(int(ys.min()), 0)
    bottom = min(int(ys.max()) + 1, height)
    region = image[top:bottom, left:right]
    if region.size == 0:
        raise QrPrepareError("检测框落在图像之外")

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    dark = gray < 128
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        raise QrPrepareError("检测框内没有深色模块")
    return (float(left + cols[0]), float(top + rows[0]),
            float(left + cols[-1] + 1), float(top + rows[-1] + 1))


def _crop_square(image: np.ndarray, points: np.ndarray,
                 side_mode: str = "min") -> np.ndarray:
    """裁出码面并加一圈纯白静默区。

    ``side_mode`` 取两个方向中较短的一边：码本身是正方形，多出来的那一边
    通常是卡片上压在码下方的账号名、装饰线之类，按较长边裁会把它们带进来。

    静默区不能用"原图多裁一圈"来做：卡片上紧贴码下方的账号名常常就落在
    那几像素里，会被一起裁进白边。所以先精确裁到码面，再贴到纯白画布上。
    """
    left, top, right, bottom = _tight_box(image, points)

    width = right - left
    height = bottom - top
    side = min(width, height) if side_mode == "min" else max(width, height)
    if side <= 0:
        raise QrPrepareError("检测到的二维码尺寸异常")

    box_left = int(round((left + right) / 2 - side / 2))
    box_top = int(round((top + bottom) / 2 - side / 2))
    code = _crop_padded(image, box_left, box_top, int(round(side)))

    margin = max(1, int(round(side * QUIET_ZONE_RATIO)))
    canvas_side = code.shape[0] + 2 * margin
    canvas = np.full((canvas_side, canvas_side, 3), 255, dtype=np.uint8)
    canvas[margin:margin + code.shape[0], margin:margin + code.shape[1]] = code
    return canvas


def _crop_padded(image: np.ndarray, left: int, top: int, side: int) -> np.ndarray:
    """从原图裁一个正方形；越界部分用白色补齐。"""
    height, width = image.shape[:2]
    pad = side
    canvas = np.full((height + 2 * pad, width + 2 * pad, 3), 255, dtype=np.uint8)
    canvas[pad:pad + height, pad:pad + width] = image
    return canvas[top + pad:top + pad + side, left + pad:left + pad + side]


def _prepare(source: Path, output_size: int = OUTPUT_SIZE) -> tuple[bytes, str]:
    """裁切 + 缩放 + 回读校验，返回 PNG 字节与解码到的内容。"""
    # cv2.imread 在 Windows 上按 ANSI 代码页打开路径，中文目录（例如
    # "Pictures\收款码"）会直接读不到；先读字节再解码，不依赖系统编码。
    recorded = np.fromfile(str(source), dtype=np.uint8)
    image = cv2.imdecode(recorded, cv2.IMREAD_COLOR) if recorded.size else None
    if image is None:
        raise QrPrepareError(f"无法读取图片：{source}")
    expected = _decode(image)
    points = cv2.QRCodeDetector().detect(image)[1]

    # 先按较短边裁（排除码下方压着的账号名/装饰线）；万一裁掉了模块导致
    # 解不出来，再退回较长边重试一次，两次都不行才报错。
    for side_mode in ("min", "max"):
        cropped = _crop_square(image, points, side_mode)
        resized = cv2.resize(cropped, (output_size, output_size),
                             interpolation=cv2.INTER_AREA)
        payload = _encode_png(resized)
        # 回读校验：裁切缩放后必须仍解出完全相同的内容。这条挡的是"图看着
        # 没坏但扫不出来"——真实世界里最难发现、代价最大的一种失败。
        actual = _decode(cv2.imdecode(np.frombuffer(payload, dtype=np.uint8),
                                      cv2.IMREAD_COLOR))
        if actual == expected:
            return payload, expected
    raise QrPrepareError(f"裁切后解码内容与原图不一致：{source}")


def _encode_png(image: np.ndarray) -> bytes:
    """BGR 数组 → 调色板 PNG 字节（不抖动，保持模块边缘干净）。"""
    rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    quantized = rgb.quantize(
        colors=PALETTE_COLORS,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    buffer = io.BytesIO()
    quantized.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(payload: bytes, name: str, *, check: bool) -> dict[Path, str]:
    """写入全部目标目录；check=True 时只比对不落盘。"""
    digests: dict[Path, str] = {}
    expected = _digest(payload)
    for directory in TARGETS:
        path = directory / name
        if check:
            if not path.is_file():
                raise QrPrepareError(f"缺少成品：{path}")
            if _digest(path.read_bytes()) != expected:
                raise QrPrepareError(f"副本与源图不一致：{path}")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        digests[path] = expected
    return digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="收款码原图所在目录")
    parser.add_argument("--check", action="store_true",
                        help="只校验仓库内两份副本是否一致，不重新生成")
    args = parser.parse_args(argv)

    try:
        for channel, source_name in CHANNELS.items():
            output_name = f"{channel}.png"
            if args.check:
                for directory in TARGETS:
                    path = directory / output_name
                    if not path.is_file():
                        raise QrPrepareError(f"缺少成品：{path}")
                first = (TARGETS[0] / output_name).read_bytes()
                for directory in TARGETS[1:]:
                    if (directory / output_name).read_bytes() != first:
                        raise QrPrepareError(
                            f"两份副本不一致：{directory / output_name}")
                print(f"[check] {output_name} 一致，sha256={_digest(first)[:16]}…")
                continue

            payload, text = _prepare(args.source / source_name)
            _write(payload, output_name, check=False)
            print(f"[ok] {channel}: {len(payload) / 1024:.1f} KB "
                  f"sha256={_digest(payload)} 内容={text[:32]}…")
    except QrPrepareError as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
