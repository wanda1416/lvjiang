"""提交内容组装：环境快照与邮箱校验。

环境快照的原则：**只放能帮助定位的技术事实**，一条身份信息都不放。
具体地——版本、源码版 commit、平台、系统大版本、架构、界面语言、Python 版本、
是否打包版；以及宿主（主窗口）愿意提供的布局名与当前工作流名。

**不放**：登录用户名、角色名、文件路径、ADB 序列号、IP、安装路径、机器名。
角色名与用户名对定位毫无帮助，却是最直接的身份泄露。
"""
from __future__ import annotations

import platform
import re
import sys

#: 与 ``server/src/feedback.mjs`` 的 EMAIL_RE 保持同一形态：不做 RFC 完备，
#: 只挡明显不是邮箱的输入；真实有效性由「能不能回信」证明。
_EMAIL_RE = re.compile(
    r"^[^\s@,;:\"'<>()\[\]]{1,64}@[^\s@,;:\"'<>()\[\]]{1,190}\.[A-Za-z]{2,10}$")

#: 环境字段的长度上限，防止异常值把 env_json 撑爆（服务端 2000 字节）。
_FIELD_LIMIT = 80


def normalize_email(value: str) -> str:
    """去空白并统一小写；空值返回空串。"""
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    """形态校验（调用方应先 :func:`normalize_email`）。"""
    return bool(_EMAIL_RE.match(value or "")) and len(value) <= 254


def _source_commit() -> str:
    """源码版补充 git commit 短哈希；打包版返回空串。

    直接读 ``.git/HEAD``（以及它指向的 ref 文件），**不派生 git 子进程**：这条
    路径在 UI 线程上跑，起进程会在某些机器上卡住一两百毫秒。
    """
    try:
        from ...constants import PROJECT_ROOT
        git_dir = PROJECT_ROOT / ".git"
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            ref_path = git_dir / ref
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8").strip()[:12]
            # 打包后或刚 clone 的仓库可能只有 packed-refs
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0][:12]
            return ""
        return head[:12] if re.fullmatch(r"[0-9a-f]{40}", head) else ""
    except Exception:  # noqa: BLE001 — 只是锦上添花的信息，取不到就留空
        return ""


def _platform_tag() -> str:
    """与账号服务一致的口径：Android 上 ``sys.platform`` 是 linux，必须显式区分。"""
    return "android" if hasattr(sys, "getandroidapilevel") else "desktop"


def _language() -> str:
    try:
        from ...i18n import current_language
        return current_language()
    except Exception:  # noqa: BLE001 — 语言只是展示口径
        return ""


def collect_environment(*, extra: dict[str, object] | None = None) -> dict[str, str]:
    """拼出提交给服务端的环境快照。

    Args:
        extra: 宿主自愿补充的键值（例如当前布局名、正在执行的工作流名）。值会被
            强制转成截断后的字符串；调用方负责不要往这里塞身份信息。

    Returns:
        ``dict[str, str]``，可直接作为 ``env`` 字段提交。
    """
    from ..version_info import get_version

    env = {
        "version": get_version(),
        "commit": _source_commit(),
        "platform": _platform_tag(),
        "os": platform.platform(terse=True),
        "arch": platform.machine(),
        "language": _language(),
        "python": platform.python_version(),
        "frozen": "1" if getattr(sys, "frozen", False) else "0",
    }
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        env[str(key)[:_FIELD_LIMIT]] = str(value)[:_FIELD_LIMIT]
    return {key: value[:_FIELD_LIMIT] for key, value in env.items() if value}
