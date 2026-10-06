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
    from java.lang import OutOfMemoryError

    try:
        got = _bridge().screenshotRgba(int(timeout_ms))
    except OutOfMemoryError as exc:
        raise MemoryError("手机截图内存不足，已中止任务；请查看运行诊断日志") from exc
    if got is None:
        from loguru import logger

        reason = _bridge().getLastScreenshotError() or "系统未返回截图"
        logger.info(f"无障碍截图失败：{reason}")
        return None
    width, height, data = got
    # Java byte[] 实现 buffer 协议，直接交给 numpy；不再先复制整屏 Python bytes。
    return int(width), int(height), data


# 手势函数一律返回「失败原因」：成功是 None，失败是设备端给出的具体原因。
# 不用 bool：失败有服务未连接、dispatchGesture 被拒、被真实触摸打断、等回调
# 超时四种来源，压成一个 Boolean 之后调用方只能猜，而这句最终要显示给用户。


def _reason(value) -> str | None:
    """把 Kotlin 的 String? 规整成 Python 的 str | None。"""
    return None if value is None else (str(value) or None)


def tap(x: int, y: int, duration_ms: int = 50) -> str | None:
    return _reason(_bridge().tap(int(x), int(y), int(duration_ms)))


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> str | None:
    return _reason(
        _bridge().swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms)))


def long_press(x: int, y: int, duration_ms: int) -> str | None:
    return _reason(_bridge().longPress(int(x), int(y), int(duration_ms)))


def hold_move(
    x1: int, y1: int, x2: int, y2: int, move_ms: int, hold_ms: int,
) -> str | None:
    """推到位后按住 hold_ms 再抬起（两段 stroke，见 A11yBridge.holdMove）"""
    return _reason(_bridge().holdMove(
        int(x1), int(y1), int(x2), int(y2), int(move_ms), int(hold_ms)))


def back() -> bool:
    """系统 BACK（performGlobalAction）"""
    return bool(_bridge().globalBack())


def home() -> bool:
    """系统 HOME（performGlobalAction）"""
    return bool(_bridge().globalHome())
