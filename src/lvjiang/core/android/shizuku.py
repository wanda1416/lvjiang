"""Shizuku 服务自动启动 — 检测、动态解析路径、幂等启动。

Shizuku 无 root 模式由 adb 引导启动（服务进程 ``shizuku_server`` 跑在 shell uid 下），
手机重启后进程死亡，需要重新引导。PC 建立端侧执行连接时按用户配置调用
:func:`ensure_shizuku_running`：只读检测、未运行时才启动一次，绝不打断正在执行的
任务。

Shizuku 13.x 服务端即原生二进制 ``libshizuku.so``（无 ``start.sh`` 遗留机制），
启动即动态解析 ``pm path`` 得到的 native lib 路径后直接后台执行（等价于手动执行
``libshizuku.so``）。检测用进程名 ``shizuku_server``（真机 ``ps -A`` 验证：
libshizuku.so 启动后 comm 为 shizuku_server、USER=shell、PPid=1 自行 daemonize）。
"""
from __future__ import annotations

import time

from loguru import logger

from .device import AdbDevice

#: Shizuku 服务包名
SHIZUKU_PACKAGE = "moe.shizuku.privileged.api"
#: Shizuku 服务进程名（真机验证，只读 pidof 用它检测）
SHIZUKU_PROCESS = "shizuku_server"

#: ``ro.product.cpu.abi`` → Shizuku native lib 目录名
_ABI_TO_LIB_DIR = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "x86_64": "x86_64",
    "x86": "x86",
}


def is_running(device: AdbDevice) -> bool:
    """Shizuku 服务是否在运行（只读 pidof，无副作用）。"""
    out = device.shell("pidof", SHIZUKU_PROCESS, timeout=5.0, retry_safe=True)
    return bool(out.strip())


def _resolve_native_lib(device: AdbDevice) -> str | None:
    """动态解析 ``libshizuku.so`` 绝对路径；未安装或解析失败返回 None。

    依赖 ``pm path``（只读）与 ``get_abi()``：把 base.apk 所在目录里的
    ``base.apk`` 换成 ``lib/<abi>/libshizuku.so``，规避每台设备/每次安装
    都不同的 ``~~...`` 随机段。
    """
    out = device.shell("pm", "path", SHIZUKU_PACKAGE, timeout=10.0, retry_safe=True)
    # 形如 package:/data/app/~~xxx/moe.shizuku.privileged.api-yyy/base.apk
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        apk = line[len("package:"):]
        if not apk.endswith("/base.apk"):
            continue
        base_dir = apk[: -len("base.apk")]
        lib_dir = _ABI_TO_LIB_DIR.get(device.get_abi(), "arm64")
        return f"{base_dir}lib/{lib_dir}/libshizuku.so"
    return None


def _launch_background(device: AdbDevice, command: str) -> None:
    """后台启动服务命令（重定向输出并 ``&``，避免 adb shell 阻塞等待服务进程）。"""
    device.shell(f"{command} >/dev/null 2>&1 &", timeout=10.0)


def ensure_shizuku_running(
    device: AdbDevice,
    *,
    timeout: float = 20.0,
) -> tuple[bool, str]:
    """检查 Shizuku 服务；未运行则启动一次。返回 ``(是否在运行, 说明)``。

    幂等：已在运行则直接返回，不重复启动。启动后轮询 ``pidof`` 确认，不假设
    立即就绪。失败（未安装 / 解析失败 / 启动后仍未就绪）只返回说明，不抛异常，
    由调用方决定是否继续连接。
    """
    if is_running(device):
        return True, "Shizuku 服务已在运行"

    lib = _resolve_native_lib(device)
    if lib is None:
        return False, (
            "未检测到 Shizuku（moe.shizuku.privileged.api），"
            "需要重启游戏的任务将无法运行"
        )
    # Shizuku 13.x 服务端即原生 libshizuku.so，直接后台执行（自行 daemonize）
    _launch_background(device, lib)
    logger.info("[Shizuku] 已启动服务: %s", lib)

    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        if is_running(device):
            return True, "Shizuku 服务已自动启动"
        time.sleep(0.5)
    return False, "Shizuku 服务启动后仍未就绪，请手动启动并授权"
