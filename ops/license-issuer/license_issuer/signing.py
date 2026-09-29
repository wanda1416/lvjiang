"""签发逻辑（与界面无关，可单独测）

激活码的正文格式与验签规则由主程序的 ``lvjiang.core.license.code`` 定义，这里**直接
复用**而不是照抄一份：格式一旦两处各写一遍，哪天改了其中一处，签出来的码就会静默地
验不过——错误现象是「用户说激活码无效」，排查起来极其费劲。
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

#: 主程序源码根。ops 下的工具是仓库内的维护工具，直接吃同一份源码，
#: 也直接用根目录那个 .venv——依赖（PyQt6 / cryptography）主项目本来就有。
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lvjiang.core.license import KNOWN_FEATURES  # noqa: E402
from lvjiang.core.license.code import (  # noqa: E402
    PUBLIC_KEY_B32,
    License,
    LicenseError,
    build_payload,
    format_code,
    verify_code,
)
from lvjiang.core.license.hardware import (  # noqa: E402
    normalize_serial,
    serial_is_wellformed,
)

__all__ = [
    "KNOWN_FEATURES",
    "DEFAULT_KEY_PATH",
    "IssueRequest",
    "License",
    "LicenseError",
    "SigningKeyError",
    "issue",
    "key_status",
    "normalize_serial",
    "serial_is_wellformed",
    "verify",
]

#: 私钥默认落点：**仓库之外**的用户目录。
#: 不放 config/local——那是被跟踪的配置仓库，密钥搁进去迟早会被误提交。
DEFAULT_KEY_PATH = Path.home() / ".lvjiang" / "license_signing_key.txt"


class SigningKeyError(Exception):
    """私钥不可用"""


@dataclass(frozen=True)
class IssueRequest:
    """一次签发请求"""

    code_id: str
    bind_to_serial: bool
    serial: str = ""
    features: tuple[str, ...] = ()
    expires: date | None = None

    def validate(self) -> str:
        """返回第一条不满足的理由；全部满足返回空串"""
        if not self.code_id.strip():
            return "请填写码编号"
        if not self.features:
            return "请至少勾选一项功能"
        if self.bind_to_serial:
            if not self.serial.strip():
                return "绑机码需要填写目标机器的序列号"
            if not serial_is_wellformed(self.serial):
                return "序列号校验位不对，多半是抄漏或抄错了一位"
        return ""


def key_status(path: Path) -> tuple[bool, str]:
    """私钥是否可用，返回 ``(可用, 说明)``"""
    if not path.is_file():
        return False, f"找不到私钥：{path}"
    try:
        _load_key(path)
    except SigningKeyError as exc:
        return False, str(exc)
    return True, f"私钥已就绪：{path}"


def _load_key(path: Path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    try:
        text = path.read_text(encoding="utf-8").strip()
        raw = base64.b32decode(text + "=" * (-len(text) % 8), casefold=True)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except OSError as exc:
        raise SigningKeyError(f"读取私钥失败：{exc}") from exc
    except Exception as exc:
        raise SigningKeyError(f"私钥内容无效：{exc}") from exc


def issue(request: IssueRequest, key_path: Path) -> str:
    """签发一张激活码"""
    problem = request.validate()
    if problem:
        raise ValueError(problem)
    private = _load_key(key_path)
    payload = build_payload(
        code_id=request.code_id.strip(),
        bind="serial" if request.bind_to_serial else "none",
        features=list(request.features),
        serial=normalize_serial(request.serial) if request.bind_to_serial else None,
        expires=request.expires,
    )
    return format_code(payload, private.sign(payload))


def verify(code: str) -> License:
    """用主程序内置的公钥验一张码——确认发出去的确实能被客户端接受"""
    if not PUBLIC_KEY_B32:
        raise LicenseError("主程序尚未注入公钥，无法自检")
    return verify_code(code)
