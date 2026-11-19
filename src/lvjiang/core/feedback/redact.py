"""日志与截图文本的脱敏。

为什么要有这一层：反馈里的日志是**整行原文**，里面天然带着本机用户目录、令牌、
设备序列号、匿名统计标识。用户指南要求「提交前遮盖四类信息」，但要求用户在两千
行日志里手工遮盖等于不遮盖——所以这件事由程序做，用户只需要在「预览」里核对。

红线（见 PRIVACY.md 与 docs/60-guide/04-desktop/09-faq-and-feedback.md）：

* 本机用户名与用户目录 → ``<用户目录>``
* Token / Cookie / 口令 → ``<已隐藏>``
* ADB 序列号（``serial: xxx`` / ``ANDROID_SERIAL``）→ ``<设备序号>``
* 匿名统计的 install_id → ``<匿名ID>``（它能让作者把一条 issue 与一条匿名记录
  对上，见 ``core/telemetry/paths.py`` 的说明）

刻意**不**脱敏的内容：内网 IP、工作流名、布局名、错误码与堆栈——它们是排查
依据，去掉之后日志就没用了。
"""
from __future__ import annotations

import re

_MASK = "<已隐藏>"
_HOME = "<用户目录>"
_SERIAL = "<设备序号>"
_ANON = "<匿名ID>"

#: Windows 用户目录（正反斜杠都出现，且可能带盘符）。
_WIN_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.IGNORECASE)
#: macOS / Linux 用户目录。
_POSIX_HOME = re.compile(r"/(?:home|Users)/[^/\s\"']+")

#: 键值形态的敏感项：``token=...`` / ``"refresh_token": "..."`` / ``serial: ...``。
_SECRET_KEY = (r"(?:access_token|refresh_token|token|cookie|password|passwd|secret"
               r"|authorization|api_key|apikey)")
_SECRET_PAIR = re.compile(
    rf"""(["']?{_SECRET_KEY}["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&]+)""", re.IGNORECASE)
#: Bearer 头单独一条：``Bearer abc...``
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
#: ADB 序列号：只匹配明确标注的写法，避免把所有长十六进制串都误伤成掩码。
_SERIAL_PAIR = re.compile(
    r"(\b(?:serial(?:no|_no)?|设备序列号|adb_serial)\b\s*[:=]\s*)([A-Za-z0-9._:-]+)",
    re.IGNORECASE)
_ANDROID_SERIAL_ENV = re.compile(r"(\bANDROID_SERIAL\s*=\s*)([A-Za-z0-9._:-]+)")
#: 模拟器序列号（形如 emulator-5554）在日志里常裸出现，但它是公开约定值，不必遮。


def _known_install_id() -> str:
    """本机匿名统计标识（拿不到就返回空串）。

    延迟导入 + 全异常兜底：脱敏是提交前的最后一步，不能因为读不到一个可选
    文件就让整条反馈卡住。
    """
    try:
        import json

        from ..telemetry.paths import identity_path
        data = json.loads(identity_path().read_text(encoding="utf-8"))
        value = data.get("install_id") if isinstance(data, dict) else ""
        return value if isinstance(value, str) else ""
    except Exception:  # noqa: BLE001 — 脱敏降级，不影响提交
        return ""


def redact_text(text: str, *, install_id: str | None = None) -> str:
    """把一段日志/文本里的身份信息替换成占位符。

    Args:
        text: 原文（日志行、诊断报告等）。
        install_id: 本机匿名标识；缺省时尝试从 telemetry 目录读取。

    Returns:
        脱敏后的文本。**不做长度截断**，截断由调用方按上限处理。
    """
    if not text:
        return ""
    result = _WIN_HOME.sub(_HOME, text)
    result = _POSIX_HOME.sub(_HOME, result)
    result = _BEARER.sub(rf"\1{_MASK}", result)
    result = _SECRET_PAIR.sub(rf"\1{_MASK}", result)
    result = _SERIAL_PAIR.sub(rf"\1{_SERIAL}", result)
    result = _ANDROID_SERIAL_ENV.sub(rf"\1{_SERIAL}", result)
    anon = install_id if install_id is not None else _known_install_id()
    if anon and len(anon) >= 8:
        result = result.replace(anon, _ANON)
    return result
