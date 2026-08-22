"""设备端 shell 通道 — Kotlin ShellBridge 的 Python 门面

设备上没有 adb 客户端，PC 端 AdbDevice 那套「起子进程调 adb」在这里不成立。
命令实际由 com.lvjiang.app.ShellBridge 经 Shizuku 下发到 shell uid 进程执行，
本模块只做一层薄封装，是全仓库唯一 import com.lvjiang.app 的截图/输入入口。

命令字面量与 PC 端保持同源（input tap / input swipe / screencap -p），
因此上层工作流在两端下发的是同一串东西，行为差异只可能来自传输层。
"""


def _bridge():
    """延迟取 ShellBridge 单例

    放在函数里而不是模块顶层：com.lvjiang.app 只在 Chaquopy 运行时存在，
    顶层导入会让本模块在 PC 上直接 import 失败（静态检查和单测都跑不了）。

    取 .INSTANCE 而不是直接用类：ShellBridge 在 Kotlin 里是 object（单例），
    编译后那些方法仍是实例方法，挂在编译器生成的静态字段 INSTANCE 上。
    直接拿类调会得到「Unbound method ... must be called with instance」。
    """
    from com.lvjiang.app import ShellBridge

    return ShellBridge.INSTANCE


def is_shizuku_alive() -> bool:
    """Shizuku 本身是否在跑（不阻塞，只 ping binder）

    无 root 的 Shizuku 被系统杀掉后就得重新激活，这是设备端最常见的失败原因，
    必须与「没授权」区分开报。
    """
    return bool(_bridge().isShizukuAlive())


def has_permission() -> bool:
    """是否已获得 Shizuku 授权"""
    return bool(_bridge().hasPermission())


def is_ready(timeout_ms: int = 5000) -> bool:
    """shell 通道是否可用（含等待异步绑定完成）

    Shizuku 未激活或未授权时返回 False —— 这是设备端最常见的失败原因，
    调用方应当在此处给出明确提示，而不是让后续命令返回一堆空字节。
    """
    return bool(_bridge().awaitReady(timeout_ms))


def exec_bytes(*cmd: str) -> bytes:
    """执行命令并返回原始 stdout 字节

    截图必须走这个而非 exec_text：PNG 是二进制，按 UTF-8 解一遍就毁了。
    Kotlin 侧是 vararg，Chaquopy 按 Java varargs 规则接收散开的位置参数。
    """
    return bytes(_bridge().exec(*cmd))


def exec_text(*cmd: str) -> str:
    return str(_bridge().execText(*cmd))


def screencap_png() -> bytes:
    """整屏 PNG 字节（等价 PC 端 adb exec-out screencap -p）"""
    return bytes(_bridge().screencapPng())


def tap(x: int, y: int) -> str:
    return str(_bridge().tap(int(x), int(y)))


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> str:
    return str(_bridge().swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms)))


def key_event(keycode: int) -> bool:
    """input keyevent <code>（4=BACK 3=HOME）；命令返回非空通常是报错文本"""
    out = exec_text("input", "keyevent", str(int(keycode)))
    return not out.strip()
