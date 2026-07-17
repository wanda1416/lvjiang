"""测试目标窗口是否响应 PostMessage/SendMessage 鼠标事件

针对 vivo 投屏等桌面应用，重点排查子窗口问题。

用法：
    python test_postmessage.py
"""

import ctypes
import ctypes.wintypes
import time

user32 = ctypes.windll.user32

# ─── Win32 消息常量 ─────────────────────────────────────
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_NCHITTEST = 0x0084
MK_LBUTTON = 0x0001
HTCLIENT = 1


def make_lparam(x: int, y: int) -> int:
    """将 (x, y) 打包为 LPARAM（低位 x，高位 y）"""
    return (y << 16) | (x & 0xFFFF)


def get_window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def list_all_visible_windows():
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = get_window_text(hwnd)
            if title:
                results.append((hwnd, title))
        return True

    user32.EnumWindows(enum_callback, 0)
    return results


def enum_child_windows(parent_hwnd):
    """枚举指定窗口的所有子窗口，返回 [(hwnd, title, class_name, rect)]"""
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        title = get_window_text(hwnd)
        cls = get_class_name(hwnd)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        results.append((hwnd, title, cls, rect))
        return True

    user32.EnumChildWindows(parent_hwnd, callback, 0)
    return results


def send_click(hwnd, client_x: int, client_y: int, use_send=False):
    """向窗口发送一次点击"""
    lparam = make_lparam(client_x, client_y)
    api = "SendMessage" if use_send else "PostMessage"
    fn = user32.SendMessageW if use_send else user32.PostMessageW

    print(f"  [{api}] click hwnd=0x{hwnd:08X} ({client_x}, {client_y})")
    fn(hwnd, WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.05)
    fn(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    fn(hwnd, WM_LBUTTONUP, 0, lparam)


def send_drag(hwnd, x1: int, y1: int, x2: int, y2: int, steps: int = 20, use_send=False):
    """向窗口发送一次拖拽（从 (x1,y1) 拖到 (x2,y2)）"""
    api = "SendMessage" if use_send else "PostMessage"
    fn = user32.SendMessageW if use_send else user32.PostMessageW

    print(f"  [{api}] drag hwnd=0x{hwnd:08X} ({x1},{y1}) -> ({x2},{y2})")

    # 移动到起点
    fn(hwnd, WM_MOUSEMOVE, 0, make_lparam(x1, y1))
    time.sleep(0.05)

    # 按下
    fn(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, make_lparam(x1, y1))
    time.sleep(0.05)

    # 逐步移动（模拟拖拽轨迹）
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(x1 + (x2 - x1) * ratio)
        cy = int(y1 + (y2 - y1) * ratio)
        fn(hwnd, WM_MOUSEMOVE, MK_LBUTTON, make_lparam(cx, cy))
        time.sleep(0.02)

    # 在终点松开
    fn(hwnd, WM_LBUTTONUP, 0, make_lparam(x2, y2))


def screen_to_client(hwnd, screen_x: int, screen_y: int):
    pt = ctypes.wintypes.POINT(screen_x, screen_y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def find_window_by_title(keyword):
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = get_window_text(hwnd)
            if keyword.lower() in title.lower():
                results.append((hwnd, title))
        return True

    user32.EnumWindows(enum_callback, 0)
    return results


# ─── 主流程 ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("PostMessage 鼠标响应测试工具（含子窗口探测）")
    print("=" * 60)

    # 第一步：列出所有可见窗口
    print("\n[1] 当前所有可见窗口：")
    all_wins = list_all_visible_windows()
    for i, (hwnd, title) in enumerate(all_wins):
        print(f"  {i:3d}. [0x{hwnd:08X}] {title}")

    # 第二步：让用户选择目标窗口
    print("\n请输入目标窗口编号（或输入窗口标题关键字）：")
    user_input = input("> ").strip()

    if user_input.isdigit():
        idx = int(user_input)
        if 0 <= idx < len(all_wins):
            target_hwnd, target_title = all_wins[idx]
        else:
            print(f"编号超出范围 (0~{len(all_wins)-1})")
            exit(1)
    else:
        matches = find_window_by_title(user_input)
        if not matches:
            print(f"未找到包含 '{user_input}' 的窗口")
            exit(1)
        target_hwnd, target_title = matches[0]

    print(f"\n目标窗口: [{target_title}] (hwnd=0x{target_hwnd:08X})")
    print(f"窗口类名: {get_class_name(target_hwnd)}")

    # 第三步：获取窗口位置
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top
    center_sx = rect.left + win_w // 2
    center_sy = rect.top + win_h // 2

    print(f"窗口位置: ({rect.left}, {rect.top}) - ({rect.right}, {rect.bottom})")
    print(f"窗口大小: {win_w} x {win_h}")
    print(f"窗口中心（屏幕坐标）: ({center_sx}, {center_sy})")

    # 第四步：枚举子窗口
    print(f"\n[2] 子窗口列表：")
    children = enum_child_windows(target_hwnd)
    if not children:
        print("  （无子窗口）")
    else:
        for i, (chwnd, ctitle, ccls, crect) in enumerate(children):
            cw = crect.right - crect.left
            ch = crect.bottom - crect.top
            print(f"  {i:3d}. [0x{chwnd:08X}] class='{ccls}' title='{ctitle}'")
            print(f"       rect=({crect.left},{crect.top})-({crect.right},{crect.bottom}) size={cw}x{ch}")

    # 第五步：找到中心点处的实际窗口（用 ChildWindowFromPoint）
    cx, cy = screen_to_client(target_hwnd, center_sx, center_sy)
    print(f"\n[3] 窗口中心客户区坐标: ({cx}, {cy})")

    # ChildWindowFromPointEx 可以穿透隐藏/禁用的子窗口
    # 先用 ScreenToClient 把屏幕坐标转为父窗口客户区坐标
    pt = ctypes.wintypes.POINT(cx, cy)
    # CWP_SKIPINVISIBLE | CWP_SKIPDISABLED = 0x0003
    child_at_point = user32.ChildWindowFromPointEx(target_hwnd, pt, 0x0003)
    if child_at_point and child_at_point != target_hwnd:
        print(f"  中心点处子窗口: 0x{child_at_point:08X} (class={get_class_name(child_at_point)})")
    else:
        print(f"  中心点处无子窗口，消息直接发给父窗口")

    # 第六步：逐个测试
    print(f"\n[4] 开始测试（3 秒倒计时，请切换到投屏窗口）...")
    time.sleep(3)

    # 构建测试目标列表：父窗口 + 所有子窗口
    targets = [(target_hwnd, "父窗口")]
    for chwnd, ctitle, ccls, crect in children:
        targets.append((chwnd, f"子窗口 class={ccls}"))

    # 如果中心点有子窗口，也加入
    if child_at_point and child_at_point != target_hwnd:
        targets.append((child_at_point, "中心点处子窗口"))

    for hwnd, desc in targets:
        print(f"\n{'='*40}")
        print(f"--- 测试: {desc} (0x{hwnd:08X}) ---")

        # 计算该窗口客户区尺寸
        wrect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wrect))
        ww = wrect.right - wrect.left
        wh = wrect.bottom - wrect.top
        cx = ww // 2
        cy = wh // 2
        print(f"  窗口大小: {ww} x {wh}")
        print(f"  中心点（客户区）: ({cx}, {cy})")

        # 拖拽范围（窗口内上下滑动 1/4 高度）
        drag_dy = wh // 4
        y_from = cy + drag_dy // 2   # 起点偏下
        y_to = cy - drag_dy // 2     # 终点偏上
        print(f"  拖拽范围: ({cx},{y_from}) -> ({cx},{y_to})")

        # 1) PostMessage 点击
        print(f"\n  [1/4] PostMessage 点击:")
        send_click(hwnd, cx, cy, use_send=False)
        time.sleep(0.3)

        # 2) PostMessage 拖拽
        print(f"  [2/4] PostMessage 拖拽:")
        send_drag(hwnd, cx, y_from, cx, y_to, steps=20, use_send=False)
        time.sleep(0.3)

        # 3) SendMessage 点击
        print(f"  [3/4] SendMessage 点击:")
        send_click(hwnd, cx, cy, use_send=True)
        time.sleep(0.3)

        # 4) SendMessage 拖拽
        print(f"  [4/4] SendMessage 拖拽:")
        send_drag(hwnd, cx, y_from, cx, y_to, steps=20, use_send=True)
        time.sleep(0.3)

        input("\n  按 Enter 继续下一个目标（观察投屏是否响应）...")

    print("\n测试完成。如果所有目标都无响应，说明该投屏应用不使用标准 Win32 消息处理鼠标。")
