"""后台模式失效全量诊断脚本（不依赖 GUI）

对目标窗口打印决定性数据，用于区分失效原因：
1. DPI awareness / 所在屏 DPI / 换算系数
2. 窗口矩形（GetWindowRect）vs 客户区（GetClientRect）的边框偏移
3. 中心点处的子窗口（投屏软件常用子窗口接收鼠标）
4. 用 PostMessage 直接点击窗口中心，观察是否响应

用法：
    python scripts/diagnostics/windows/postmessage.py "标题关键字"
    python scripts/diagnostics/windows/postmessage.py   # 列出所有窗口后选择
"""

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore

# 消息常量
_WM_NCHITTEST = 0x0084
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001

DPI_AWARENESS = {0: "UNAWARE", 1: "SYSTEM_AWARE", 2: "PER_MONITOR_AWARE"}

# 优先复用生产代码的 activate_window（验证真实修复）；失败则本地兜底
try:
    sys.path.insert(0, "src")
    from lvjiang.core.desktop.win32_util import activate_window
except Exception:  # pragma: no cover - 非项目环境兜底
    def activate_window(hwnd, restore=True):
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        return True


def make_lparam(x, y):
    return (y << 16) | (x & 0xFFFF)


def list_visible_windows():
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                results.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return results


def win_dpi_awareness(hwnd):
    try:
        user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        ctx = user32.GetWindowDpiAwarenessContext(wintypes.HWND(hwnd))
        if not ctx:
            return -1
        return user32.GetAwarenessFromDpiAwarenessContext(ctx)
    except Exception:
        return -1


def monitor_dpi(hwnd):
    try:
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        hmon = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return 96
        xd = ctypes.c_uint(0)
        yd = ctypes.c_uint(0)
        shcore.GetDpiForMonitor.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
        ]
        shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(xd), ctypes.byref(yd))  # MDT_EFFECTIVE_DPI
        return int(xd.value or 96)
    except Exception:
        return 96


def get_dpi_for_window(hwnd):
    """GetDpiForWindow：unaware→96，system→系统DPI，per-monitor→所在屏DPI"""
    try:
        user32.GetDpiForWindow.restype = ctypes.c_uint
        return int(user32.GetDpiForWindow(wintypes.HWND(hwnd)) or 96)
    except Exception:
        return 96


def get_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def get_client(hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r


def client_to_screen(hwnd, cx, cy):
    pt = wintypes.POINT(cx, cy)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def postmessage_click(hwnd, cx, cy, use_send=False):
    """PostMessage 或 SendMessage 投递一次点击。

    use_send=True 时用 SendMessage（同步，等待目标处理完），
    对部分 GPU 渲染窗口（Qt/SDL）比 PostMessage 更可能被处理。
    """
    lparam = make_lparam(cx, cy)
    fn = user32.SendMessageW if use_send else user32.PostMessageW
    fn(hwnd, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.05)
    fn(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    time.sleep(0.05)
    fn(hwnd, _WM_LBUTTONUP, 0, lparam)


def full_sequence_click(hwnd, cx, cy, use_send=False):
    """含 WM_NCHITTEST 的完整系统消息序列。

    先让窗口走 DefWindowProc 命中测试（建立内部点击上下文），
    再发 mouse move + down + up，更接近真实系统点击。
    """
    lparam = make_lparam(cx, cy)
    fn = user32.SendMessageW if use_send else user32.PostMessageW
    user32.SendMessageW(hwnd, _WM_NCHITTEST, 0, lparam)
    fn(hwnd, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.05)
    fn(hwnd, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.05)
    fn(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    time.sleep(0.08)
    fn(hwnd, _WM_LBUTTONUP, 0, lparam)


def child_at(hwnd, client_pt):
    try:
        user32.ChildWindowFromPointEx.restype = ctypes.c_void_p
        child = user32.ChildWindowFromPointEx(
            wintypes.HWND(hwnd), client_pt, 0x0003  # CWP_SKIPINVISIBLE|CWP_SKIPDISABLED
        )
        return child if child and child != hwnd else None
    except Exception:
        return None


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def main():
    wins = list_visible_windows()
    print("=" * 72)
    print("候选窗口：")
    for i, (hwnd, title) in enumerate(wins):
        print(f"  {i:3d}. [0x{hwnd:08X}] {title}")
    print()

    if len(sys.argv) > 1:
        kw = sys.argv[1].lower()
        target = next(((h, t) for h, t in wins if kw in t.lower()), None)
        if not target:
            print(f"未找到标题含 '{sys.argv[1]}' 的窗口")
            return
        hwnd, title = target
    else:
        idx = int(input("输入窗口编号: ").strip())
        hwnd, title = wins[idx]

    print("=" * 72)
    print(f"目标: [{title}] hwnd=0x{hwnd:08X} class={class_name(hwnd)}")
    print()

    # 1) DPI
    aw = win_dpi_awareness(hwnd)
    mdpi = monitor_dpi(hwnd)
    gdpi = get_dpi_for_window(hwnd)
    print(f"[1] DPI awareness      : {DPI_AWARENESS.get(aw, aw)}")
    print(f"    所在屏真实DPI      : {mdpi}  ({mdpi/96:.2f}x)")
    print(f"    GetDpiForWindow    : {gdpi}")
    if aw == 0:
        print(f"    PostMessage需缩放  : x{96/mdpi:.3f}")
    elif aw == 1:
        print(f"    PostMessage需缩放  : x{96/mdpi:.3f}（system 基准=96）")
    else:
        print("    PostMessage不需缩放: 1.000（per-monitor 坐标一致）")
    print()

    # 2) 窗口矩形 vs 客户区
    wr = get_rect(hwnd)
    cr = get_client(hwnd)
    cw, ch = cr.right - cr.left, cr.bottom - cr.top
    print(f"[2] GetWindowRect  : ({wr.left},{wr.top})-({wr.right},{wr.bottom})  {wr.right-wr.left}x{wr.bottom-wr.top}")
    print(f"    GetClientRect  : {cw}x{ch}")
    print("    边框偏移(顶/左): 窗口顶部到客户区顶差")
    # 客户区原点在屏幕上的位置
    c0sx, c0sy = client_to_screen(hwnd, 0, 0)
    print(f"    客户区原点屏幕坐标: ({c0sx},{c0sy})  窗口矩形左上: ({wr.left},{wr.top})")
    print(f"    顶部边框偏移: {c0sy - wr.top}px   左边框偏移: {c0sx - wr.left}px")
    print()

    # 3) 中心点子窗口
    cx, cy = cw // 2, ch // 2
    child = child_at(hwnd, wintypes.POINT(cx, cy))
    print(f"[3] 客户区中心: ({cx},{cy})")
    if child:
        print(f"    中心点处有子窗口: 0x{child:08X} class={class_name(child)}  ← 鼠标消息需投给子窗口")
    else:
        print("    中心点无子窗口，PostMessage 投给顶层窗口即可")
    print()

    # 4) 多种投递方式对比测试
    #    每个用例间隔留足时间观察：普通点击在手机屏幕上通常会有一个
    #    短暂的高亮/涟漪，scrcpy 画面会闪一下。
    print("[4] 多种投递方式对比（每次间隔 4 秒，请观察画面是否闪/响应）：")
    print("    标记格式: (方式) 目标 @ 坐标")
    time.sleep(6)

    cases = [
        ("A", "PostMessage", "顶层", hwnd, cx, cy, False, postmessage_click),
        ("B", "SendMessage ", "顶层", hwnd, cx, cy, True, postmessage_click),
        ("C", "Post+全序列 ", "顶层", hwnd, cx, cy, False, full_sequence_click),
        ("D", "Send+全序列 ", "顶层", hwnd, cx, cy, True, full_sequence_click),
    ]

    if child:
        ch_cr = get_client(child)
        ch_w, ch_h = ch_cr.right - ch_cr.left, ch_cr.bottom - ch_cr.top
        sub_cx, sub_cy = ch_w // 2, ch_h // 2
        cases += [
            ("E", "PostMessage", f"子窗{ch_w}x{ch_h}", child, sub_cx, sub_cy, False, postmessage_click),
            ("F", "SendMessage ", f"子窗{ch_w}x{ch_h}", child, sub_cx, sub_cy, True, postmessage_click),
            ("G", "Send+全序列 ", f"子窗{ch_w}x{ch_h}", child, sub_cx, sub_cy, True, full_sequence_click),
        ]

    for tag, mode, target_desc, target_h, tx, ty, use_send, click_fn in cases:
        print(f"    ({tag}) {mode} {target_desc} 0x{target_h:08X} @ ({tx},{ty})")
        click_fn(target_h, tx, ty, use_send)
        print(f"        已发送，{4}秒后测下一个...")
        time.sleep(4)

    print("    全部测完。请告诉我哪些字母(A~G)让画面响应了。")
    print()

    # 5) 瞬时激活后投递（生产代码 activate_window 方案）
    print("[5] 瞬时激活窗口后投递（生产代码方案）...")
    time.sleep(2)
    # 激活（不还原，观察 SDL 是否因激活而开始处理）
    activate_window(hwnd, restore=False)
    print(f"    已激活窗口 0x{hwnd:08X}（还原=关），等待 2 秒...")
    time.sleep(2)
    postmessage_click(hwnd, cx, cy)
    time.sleep(1)
    postmessage_click(hwnd, cx, cy)
    print("    已激活后投递两次点击。请观察画面是否响应。")
    print("    若响应，说明“点击前瞬时激活”方案可行。")


if __name__ == "__main__":
    main()
