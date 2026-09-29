"""激活码编解码与验签（Ed25519）

激活码结构：``LVJ1.<base32(payload)>.<base32(signature)>``

- ``payload`` 是紧凑 JSON，字段短但自描述；出问题时肉眼就能看出这张码发的是什么，
  不用翻代码里的位定义。二进制打包能把码从约 210 字符压到约 135，但两者都早已
  超出「能手输」的范围、同属粘贴场景，省这 70 个字符不值得牺牲可读性。
- 分组用 ``.``，签名与正文分开，方便定位是正文被改了还是签名不匹配。
- base32 用标准字母表（大写 + 2-7），去掉 padding；比 base64 少一类大小写混淆。

**验签只用公钥**，私钥永远留在签发者手里，不进仓库、不进发行包。

这套东西挡的是「把激活码转手发给别人」，不挡逆向——客户端校验天然可以被绕过，
所以这里不做混淆、不做完整性自检，保持明文可读。
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import date

from loguru import logger

#: 码格式版本，换格式时用它区分
_PREFIX = "LVJ1"

#: 签发者公钥（base32 编码的 32 字节 Ed25519 公钥）。
#: 由 ``scripts/issue_license.py keygen`` 生成后粘贴到这里；留空表示尚未配置签发
#: 密钥——此时所有激活码一律不通过，高级功能整体关闭，而不是报错崩溃。
PUBLIC_KEY_B32 = ""


class LicenseError(Exception):
    """激活码不可用（格式、签名、绑定或有效期任一不满足）"""


@dataclass(frozen=True)
class License:
    """一张已验签通过的激活码"""

    version: int
    code_id: str
    bind: str                       # "serial"（绑机）或 "none"（免绑定）
    serial: str | None              # bind="serial" 时为绑定的序列号
    features: tuple[str, ...] = ()
    expires: date | None = None

    @property
    def is_bound(self) -> bool:
        return self.bind == "serial"

    def expired(self, today: date | None = None) -> bool:
        if self.expires is None:
            return False
        return (today or date.today()) > self.expires

    def has(self, feature: str) -> bool:
        return feature in self.features


def _b32encode(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _b32decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError(f"激活码编码无效: {exc}") from exc


def normalize_code(text: str) -> str:
    """去掉空白与分组用的连字符，便于接受用户从聊天记录里粘来的内容"""
    return "".join(text.split()).replace("-", "")


def build_payload(
    code_id: str,
    bind: str,
    features: list[str],
    serial: str | None = None,
    expires: date | None = None,
) -> bytes:
    """构造待签名正文（签发工具用；顺序固定，保证同输入同字节）"""
    if bind not in ("serial", "none"):
        raise ValueError(f"未知的绑定模式: {bind!r}")
    if bind == "serial" and not serial:
        raise ValueError("绑机码必须提供序列号")
    data: dict[str, object] = {
        "v": 1,
        "id": code_id,
        "bind": bind,
        "feat": list(features),
    }
    if bind == "serial":
        data["sn"] = serial
    if expires is not None:
        data["exp"] = expires.isoformat()
    return json.dumps(data, separators=(",", ":"), sort_keys=True,
                      ensure_ascii=False).encode("utf-8")


def format_code(payload: bytes, signature: bytes) -> str:
    """拼成最终激活码"""
    return f"{_PREFIX}.{_b32encode(payload)}.{_b32encode(signature)}"


def _parse_payload(raw: bytes) -> License:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseError(f"激活码正文无法解析: {exc}") from exc
    if not isinstance(data, dict):
        raise LicenseError("激活码正文不是对象")
    bind = str(data.get("bind", ""))
    if bind not in ("serial", "none"):
        # 显式枚举而不是「sn 为空就当免绑定」：空值可能来自签发工具的 bug，
        # 免绑定必须是签发时明确写下的意图
        raise LicenseError(f"未知的绑定模式: {bind!r}")
    expires = None
    if data.get("exp"):
        try:
            expires = date.fromisoformat(str(data["exp"]))
        except ValueError as exc:
            raise LicenseError(f"有效期格式无效: {data['exp']!r}") from exc
    features = data.get("feat") or []
    if not isinstance(features, list):
        raise LicenseError("功能集必须是列表")
    return License(
        version=int(data.get("v", 0)),
        code_id=str(data.get("id", "")),
        bind=bind,
        serial=str(data["sn"]) if data.get("sn") else None,
        features=tuple(str(f) for f in features),
        expires=expires,
    )


def verify_code(code: str, public_key_b32: str | None = None) -> License:
    """验签并解析激活码；任何一步不满足都抛 :class:`LicenseError`。

    只做「这张码是不是签发者签的、内容是什么」，**不判断是否属于本机**——
    绑机比对交给上层，因为那需要硬件信息，属于另一层职责。
    """
    key_b32 = PUBLIC_KEY_B32 if public_key_b32 is None else public_key_b32
    if not key_b32:
        raise LicenseError("未配置签发公钥，无法校验激活码")

    parts = normalize_code(code).split(".")
    if len(parts) != 3 or parts[0].upper() != _PREFIX:
        raise LicenseError("激活码格式不正确")
    payload = _b32decode(parts[1])
    signature = _b32decode(parts[2])

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    try:
        key = Ed25519PublicKey.from_public_bytes(_b32decode(key_b32))
    except Exception as exc:
        raise LicenseError(f"内置公钥无效: {exc}") from exc
    try:
        key.verify(signature, payload)
    except InvalidSignature as exc:
        raise LicenseError("激活码签名不匹配") from exc

    license_ = _parse_payload(payload)
    if license_.version != 1:
        raise LicenseError(f"不支持的激活码版本: {license_.version}")
    return license_


@dataclass(frozen=True)
class Entitlement:
    """当前进程的授权结论"""

    license: License | None = None
    reason: str = ""
    features: frozenset[str] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        return self.license is not None

    def has(self, feature: str) -> bool:
        return feature in self.features


def evaluate(
    code: str | None,
    serial: str | None,
    today: date | None = None,
) -> Entitlement:
    """把「一张码 + 本机序列号」判成最终授权结论。

    免绑定码不看序列号，所以取不到硬件标识（非 Windows、虚拟机固件异常）也能激活；
    绑机码则必须和本机序列号一致。
    """
    if not code:
        return Entitlement(reason="未填写激活码")
    try:
        license_ = verify_code(code)
    except LicenseError as exc:
        logger.info(f"激活码校验未通过: {exc}")
        return Entitlement(reason=str(exc))
    if license_.expired(today):
        return Entitlement(reason=f"激活码已于 {license_.expires} 过期")
    if license_.is_bound:
        if not serial:
            return Entitlement(reason="读不到本机序列号，绑机激活码无法校验")
        from .hardware import normalize_serial
        if normalize_serial(license_.serial or "") != normalize_serial(serial):
            return Entitlement(reason="激活码绑定的不是本机")
    return Entitlement(license=license_, features=frozenset(license_.features))
