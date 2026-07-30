"""设备端无障碍通道 — Kotlin A11yBridge 的 Python 门面

截图与点击的主通道。选它而不是 Shizuku 的理由：Shizuku 的无 root 模式必须由
adb 引导启动（本质是个跑在 shell uid 的 app_process 进程），手机重启一次就失效，
要用户重新配对无线调试；无障碍服务只要在设置里开一次，开关持久化在 secure
settings 里，重启保留，开发期还能用 adb 直接写入而不依赖人工点击。

shell.py 那条通道保留为可选的高级通道（screencap 不受截图节流限制），
两者接口形状一致，上层可以按可用性择一。
"""


def _bridge():
    """延迟取 A11yBridge 单例

    放在函数里而不是模块顶层：com.lvjiang.app 只在 Chaquopy 运行时存在，
    顶层导入会让本模块在 PC 上直接 import 失败。

    取 .INSTANCE 而不是直接用类：A11yBridge 在 Kotlin 里是 object（单例），
    编译后那些方法仍是实例方法，挂在编译器生成的静态字段 INSTANCE 上。
    """
    from com.lvjiang.app import A11yBridge

    return A11yBridge.INSTANCE


def is_ready() -> bool:
    """无障碍服务是否已连接

    这是通道唯一的可用判据：服务实例由系统在开关打开时创建，拿不到就说明
    开关没开或被系统关掉了。
    """
    try:
        return bool(_bridge().isReady())
    except Exception as e:
        print(f"[a11y] 取 A11yBridge 失败: {e}")
        return False


def capabilities() -> str:
    """服务能力位，用于确认配置 xml 里的 flag 真的生效"""
    return str(_bridge().capabilities())


def screenshot_rgba(timeout_ms: int = 5000):
    """整屏截图，返回 (宽, 高, RGBA 字节)；失败返回 None

    Kotlin 侧回的是 Object[]{Int, Int, byte[]}，Chaquopy 映射成 Python list。
    """
    got = _bridge().screenshotRgba(int(timeout_ms))
    if got is None:
        return None
    width, height, data = got
    return int(width), int(height), bytes(data)


def tap(x: int, y: int, duration_ms: int = 50) -> bool:
    return bool(_bridge().tap(int(x), int(y), int(duration_ms)))


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
    return bool(_bridge().swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms)))


def long_press(x: int, y: int, duration_ms: int) -> bool:
    return bool(_bridge().longPress(int(x), int(y), int(duration_ms)))
