"""屏幕映射标定 — 在截图里找设备端准星，反推"截图坐标 → 输入坐标"的仿射参数

PC 端在截图上量出的坐标，发给手机做手势时默认假设截图空间 == 输入空间。多数设备成立；
个别设备因挖孔处理 / 截图缩放 / 黑边会偏，点下去落在旁边。设备端 ScreenMap 为此按
机型 + 朝向分辨率存一份逐轴仿射 ``input% = shot% * s + o``，在手势注入口统一施加。

本模块负责把这四个数**量出来**（人眼看截图里准星压没压住目标也行，这里直接找准星）：

1. ``calib_clear`` → 映射恒等，此时 ``calib_mark(x, y)`` 的准星就画在输入坐标 (x, y) 上；
2. 对屏幕对角两个探针点各 mark → 截图 → 按准星颜色（洋红 #FF00E5）找到它在截图里的中心；
3. 每轴拟合 ``shot = a * input + b``，求逆得 ``s = 1/a, o = -b/a``；偏差在像素级以内即恒等；
4. ``--apply`` 时写回设备（``calib_set``），再 mark 一个第三点验证准星落在目标上。

百分比的分母统一用设备端报告的屏幕尺寸（``calib_get().screen``），与 ScreenMap.mapPoint
的归一化口径一致——截图若被缩放，比例差会被吸收进 s 里。

命令行（开发/排障用）::

    python -m lvjiang.core.android.calib [-s SERIAL] probe [--apply]
    python -m lvjiang.core.android.calib get | clear | set SX OX SY OY
    python -m lvjiang.core.android.calib mark X Y [--tap] | verify X Y | hide
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from loguru import logger

from .agent import AgentCapture, AgentClient, AgentError, connect_agent
from .device import AdbDevice

#: 准星颜色（BGR），与 CalibOverlay.MARK_COLOR（ARGB FF FF 00 E5）同值
MARK_BGR = (0xE5, 0x00, 0xFF)
#: 按色相找准星而不是按精确 RGB：Android 12+ 会把悬浮窗按 ~0.8 不透明度合成，截图里
#: 准星是 (208,4,187) 这类变暗的洋红，精确色匹配一个像素都对不上；色相（OpenCV 0–179
#: 制，洋红 ≈153）与饱和度不受亮度影响。
_MARK_HUE = 153
_HUE_TOL = 8
_MIN_SAT = 140
_MIN_VAL = 80
#: 十字臂长（CalibOverlay.ARM_PX），一条臂的像素数是"找到了"的最低门槛
_ARM_PX = 60
#: 探针点在屏幕上的相对位置：对角两点，离边缘足够远，准星不会被裁掉
_PROBE_POINTS = ((0.2, 0.2), (0.8, 0.8))
#: 验证点：不与探针点共线
_VERIFY_POINT = (0.5, 0.65)
#: 拟合后与恒等的最大偏差（像素）低于此值视为恒等，不写文件
_IDENTITY_TOL_PX = 2.0
#: mark 后等一帧再截图（覆盖层 invalidate 是异步的）
_SETTLE_S = 0.25


class CalibError(RuntimeError):
    pass


@dataclass
class CalibResult:
    """一次 probe 的结果；``params`` 为 (sx, ox, sy, oy)"""

    screen: tuple[int, int]
    shot_size: tuple[int, int]
    samples: list[tuple[tuple[int, int], tuple[float, float]]] = field(default_factory=list)
    params: tuple[float, float, float, float] = (1.0, 0.0, 1.0, 0.0)
    max_dev_px: float = 0.0
    applied: bool = False
    verify_err_px: float | None = None

    @property
    def identity(self) -> bool:
        return self.max_dev_px < _IDENTITY_TOL_PX

    def summary(self) -> str:
        sx, ox, sy, oy = self.params
        pts = "; ".join(f"输入({ix},{iy}) → 截图({sx_:.1f},{sy_:.1f})"
                        for (ix, iy), (sx_, sy_) in self.samples)
        head = (f"屏幕 {self.screen[0]}x{self.screen[1]}，截图 {self.shot_size[0]}x{self.shot_size[1]}；"
                f"{pts}；最大偏差 {self.max_dev_px:.1f}px")
        if self.identity:
            body = "→ 截图空间与输入空间一致，保持恒等映射"
        else:
            body = f"→ 映射 sx={sx:.5f} ox={ox:.5f} sy={sy:.5f} oy={oy:.5f}"
        tail = ""
        if self.applied:
            tail = "（已写入设备"
            if self.verify_err_px is not None:
                tail += f"，验证点误差 {self.verify_err_px:.1f}px"
            tail += "）"
        return f"{head} {body}{tail}"


def find_crosshair(img: np.ndarray, expected: tuple[float, float],
                   radius: int | None = None) -> tuple[float, float] | None:
    """在 ``expected`` 附近找准星中心（截图像素坐标）；找不到返回 None

    十字臂是 3px 宽的纯色直线：取准星色掩码后，列方向像素数最多的那几列就是竖臂、
    行方向最多的那几行就是横臂——比质心稳（标签文字/圆环不会把它带偏，局部遮挡也不怕）。
    """
    h, w = img.shape[:2]
    if radius is None:
        radius = max(_ARM_PX * 3, int(min(w, h) * 0.15))
    ex, ey = expected
    x0 = max(0, int(ex) - radius)
    y0 = max(0, int(ey) - radius)
    x1 = min(w, int(ex) + radius)
    y1 = min(h, int(ey) + radius)
    if x1 <= x0 or y1 <= y0:
        return None
    mask = _mark_mask(img[y0:y1, x0:x1, :3])
    cols = mask.sum(axis=0)
    rows = mask.sum(axis=1)
    # 一条臂有 2*ARM 像素；截图被缩放时臂会变短，门槛放宽到一半
    floor = _ARM_PX
    if cols.max() < floor or rows.max() < floor:
        return None
    cx = _peak_center(cols)
    cy = _peak_center(rows)
    return x0 + cx, y0 + cy


def _mark_mask(bgr: np.ndarray) -> np.ndarray:
    """准星色掩码（色相 + 饱和度 + 亮度下限）"""
    hsv = cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR2HSV)
    h = hsv[..., 0].astype(np.int16)
    hue_ok = np.abs(h - _MARK_HUE) <= _HUE_TOL
    return hue_ok & (hsv[..., 1] >= _MIN_SAT) & (hsv[..., 2] >= _MIN_VAL)


def _peak_center(profile: np.ndarray) -> float:
    """取 profile 峰值附近（≥90% 峰值）各下标的均值：3px 线宽的中心落在中间那列"""
    peak = profile.max()
    idx = np.flatnonzero(profile >= peak * 0.9)
    # 只取与最大值相邻的一簇，防止圆环上远处的列凑巧也够高
    best = int(np.argmax(profile))
    cluster = idx[np.abs(idx - best) <= 4]
    return float(cluster.mean())


def solve_axis(samples: list[tuple[float, float]]) -> tuple[float, float]:
    """由 (input%, shot%) 样本拟合 shot = a*input + b，返回逆映射 (s, o)：input = shot*s + o"""
    xs = np.array([p[0] for p in samples], dtype=np.float64)
    ys = np.array([p[1] for p in samples], dtype=np.float64)
    if len(samples) < 2 or np.ptp(xs) < 1e-9:
        raise CalibError("探针点不足以拟合")
    a, b = np.polyfit(xs, ys, 1)
    if abs(a) < 1e-6:
        raise CalibError("拟合斜率为 0：截图里的准星没随输入坐标移动")
    return 1.0 / a, -b / a


def _screenshot(capture: AgentCapture) -> np.ndarray:
    time.sleep(_SETTLE_S)
    frame = capture.capture()
    if frame is None:
        raise CalibError("截图失败")
    return frame


def probe(client: AgentClient, apply: bool = False, via: str = "auto") -> CalibResult:
    """量出当前朝向下的屏幕映射；apply=True 时写回设备并验证。结束时撤掉覆盖层。"""
    capture = AgentCapture(client, via=via)
    info = client.calib_get()
    screen = (int(info["screen"]["w"]), int(info["screen"]["h"]))
    sw, sh = screen
    previous = info.get("calib") or {}
    # 悬浮球藏起来，免得被截进准星检测窗口
    try:
        client.set_float_icon(True)
    except AgentError:
        pass
    try:
        # 探针期间必须恒等：准星才画在"原始输入坐标"上
        if not info.get("identity", True):
            client.calib_clear()
        samples: list[tuple[tuple[int, int], tuple[float, float]]] = []
        shot_size: tuple[int, int] | None = None
        for fx, fy in _PROBE_POINTS:
            ix, iy = int(sw * fx), int(sh * fy)
            client.calib_mark(ix, iy)
            frame = _screenshot(capture)
            fh, fw = frame.shape[:2]
            shot_size = (fw, fh)
            expected = (ix * fw / sw, iy * fh / sh)
            found = find_crosshair(frame, expected)
            if found is None:
                raise CalibError(
                    f"截图里找不到准星（输入点 {ix},{iy}）：确认律匠 app 已授予悬浮窗权限、"
                    f"屏幕亮着且没有全屏遮挡")
            samples.append(((ix, iy), found))
        assert shot_size is not None
        result = CalibResult(screen=screen, shot_size=shot_size, samples=samples)
        sx, ox = solve_axis([(ix / sw, fx_ / sw) for (ix, _), (fx_, _) in samples])
        sy, oy = solve_axis([(iy / sh, fy_ / sh) for (_, iy), (_, fy_) in samples])
        result.params = (sx, ox, sy, oy)
        result.max_dev_px = max(
            max(abs(fx_ - ix), abs(fy_ - iy)) for (ix, iy), (fx_, fy_) in samples)
        logger.info(f"[Calib] {result.summary()}")
        if not apply:
            # 没要求写回：把探针前的映射恢复原样
            if previous and not info.get("identity", True):
                client.calib_set(**previous)
            return result

        if result.identity:
            client.calib_clear()
        else:
            client.calib_set(sx, ox, sy, oy)
        result.applied = True
        # 验证：mark 截图空间里的目标点，映射后准星应落回该点
        tx, ty = int(sw * _VERIFY_POINT[0]), int(sh * _VERIFY_POINT[1])
        client.calib_mark(tx, ty)
        frame = _screenshot(capture)
        fh, fw = frame.shape[:2]
        found = find_crosshair(frame, (tx * fw / sw, ty * fh / sh))
        if found is not None:
            result.verify_err_px = max(abs(found[0] - tx), abs(found[1] - ty))
        logger.info(f"[Calib] {result.summary()}")
        return result
    finally:
        try:
            client.calib_hide()
        except AgentError as e:
            logger.warning(f"[Calib] 撤覆盖层失败: {e}")
        try:
            client.set_float_icon(False)
        except AgentError:
            pass


# ─── 命令行 ───────────────────────────────────────────────

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lvjiang-calib", description=__doc__.split("\n\n")[0])
    parser.add_argument("-s", "--serial", help="adb 设备序列号（多台时必填）")
    parser.add_argument("--via", default="auto", choices=("auto", "a11y", "shell"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="自动量映射；--apply 写回设备")
    p.add_argument("--apply", action="store_true")
    sub.add_parser("get")
    sub.add_parser("clear")
    p = sub.add_parser("set")
    for name in ("sx", "ox", "sy", "oy"):
        p.add_argument(name, type=float)
    p = sub.add_parser("mark", help="在 (X,Y) 映射后的落点画准星")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--tap", action="store_true")
    p = sub.add_parser("verify", help="mark (X,Y) 并在截图里找准星，报告误差")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    sub.add_parser("hide")
    p = sub.add_parser("float", help="显隐设备端悬浮球")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hide", dest="hidden", action="store_true", default=True)
    g.add_argument("--show", dest="hidden", action="store_false")
    args = parser.parse_args(argv)

    client = connect_agent(AdbDevice(args.serial))
    if client is None:
        print("连不上设备端代理（律匠 app 没在跑 / 无障碍未开？）", file=sys.stderr)
        return 2
    try:
        if args.cmd == "probe":
            print(probe(client, apply=args.apply, via=args.via).summary())
        elif args.cmd == "get":
            print(json.dumps(client.calib_get(), ensure_ascii=False, indent=2))
        elif args.cmd == "clear":
            print(json.dumps(client.calib_clear(), ensure_ascii=False))
        elif args.cmd == "set":
            print(json.dumps(client.calib_set(args.sx, args.ox, args.sy, args.oy), ensure_ascii=False))
        elif args.cmd == "mark":
            print(json.dumps(client.calib_mark(args.x, args.y, tap=args.tap, via=args.via), ensure_ascii=False))
        elif args.cmd == "verify":
            client.calib_mark(args.x, args.y)
            frame = _screenshot(AgentCapture(client, via=args.via))
            found = find_crosshair(frame, (args.x, args.y))
            if found is None:
                print("截图里找不到准星")
                return 1
            print(f"准星在截图 ({found[0]:.1f}, {found[1]:.1f})，误差 "
                  f"dx={found[0] - args.x:+.1f} dy={found[1] - args.y:+.1f}")
        elif args.cmd == "hide":
            client.calib_hide()
        elif args.cmd == "float":
            running = client.set_float_icon(args.hidden)
            print(f"悬浮球 {'隐藏' if args.hidden else '显示'}（悬浮服务{'在跑' if running else '未启动'}）")
        return 0
    except (AgentError, CalibError) as e:
        print(f"失败: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(_main())
