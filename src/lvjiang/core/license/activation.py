"""在线认领与服务器签名记录；原码和回复都验签，离线不信任布尔状态。"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from . import code as protocol
from .code import (
    Entitlement,
    LicenseError,
    _b32decode,
    normalize_code,
    verify_code,
)
from .hardware import normalize_serial, serial_is_wellformed
from .store import load_activation, load_code, save_activation

STATUS_REASON = {
    "disabled": "激活码已被云端停用", "expired": "激活码已过期",
    "unregistered": "激活码尚未登记，请联系签发者",
    "already_bound": "激活码已绑定其他设备", "unactivated": "激活码尚未在线绑定",
}


def code_hash(code: str) -> str:
    return hashlib.sha256(normalize_code(code).upper().encode("utf-8")).hexdigest()


def verify_receipt(code: str, serial: str | None, receipt: Any, *,
                   nonce: str | None = None, now: float | None = None) -> dict[str, Any]:
    """验原始字节及用途；nonce 只用于新的在线回复，离线记录仍验证绑定和期限。"""
    if not isinstance(code, str) or not code:
        raise LicenseError("本地激活记录缺少原码")
    issued = verify_code(code)
    if issued.bind != "claim" or not serial or not serial_is_wellformed(serial):
        raise LicenseError("一次性激活码需要有效的本机序列号")
    try:
        if not isinstance(receipt, dict):
            raise ValueError
        raw = _b32decode(receipt["payload"])
        signature = _b32decode(receipt["signature"])
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(_b32decode(protocol.ACTIVATION_PUBLIC_KEY_B32)).verify(signature, raw)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("v") != 1 \
                or data.get("purpose") != "license_activation_response" \
                or data.get("code_hash") != code_hash(code) or data.get("code_id") != issued.code_id \
                or data.get("serial") != normalize_serial(serial) \
                or not isinstance(data.get("nonce"), str) \
                or nonce is not None and data["nonce"] != nonce \
                or type(data.get("server_time")) is not int \
                or data.get("status") not in {"active", *STATUS_REASON}:
            raise ValueError
        if data["status"] == "active":
            if data.get("features") != list(issued.features) or data.get("days") != issued.days \
                    or type(data.get("activated_at")) is not int \
                    or type(data.get("expires_at")) is not int \
                    or data["activated_at"] > data["server_time"] \
                    or data["expires_at"] != data["activated_at"] + (issued.days or 0) * 86400:
                raise ValueError
            if (time.time() if now is None else now) >= data["expires_at"] \
                    or data["server_time"] >= data["expires_at"]:
                raise LicenseError("激活码已过期")
    except LicenseError:
        raise
    except Exception:
        raise LicenseError("服务器授权回复验签或请求关联校验失败") from None
    return data


def receipt_entitlement(code: str, serial: str | None, receipt: Any) -> Entitlement:
    try:
        data = verify_receipt(code, serial, receipt)
        if data["status"] != "active":
            return Entitlement(reason=STATUS_REASON[data["status"]])
        issued = verify_code(code)
        return Entitlement(license=issued, features=frozenset(issued.features), expires_at=data["expires_at"])
    except LicenseError as error:
        return Entitlement(reason=str(error))


def stored_entitlement(serial: str | None) -> Entitlement:
    activation = load_activation()
    if activation is not None:
        return receipt_entitlement(activation.get("code", ""), serial, activation.get("receipt"))
    return protocol.evaluate(load_code(), serial)


def activate_online(code: str, serial: str | None) -> Entitlement:
    from ..config.content_service import CONTENT_SERVICE_URL, ServiceError, request_json
    nonce = secrets.token_hex(16)
    if not serial or not serial_is_wellformed(serial):
        return Entitlement(reason="读不到有效的本机序列号，无法在线绑定")
    try:
        receipt = request_json(CONTENT_SERVICE_URL, "/v1/activate",
                               {"code": code, "serial": normalize_serial(serial), "nonce": nonce})
        verify_receipt(code, serial, receipt, nonce=nonce)
        result = receipt_entitlement(code, serial, receipt)
        if result.active and not save_activation(code, receipt):
            return Entitlement(reason="云端绑定成功，但本地保存失败，请同机重试")
        if not result.active:
            accept_status(code, normalize_serial(serial), receipt, nonce)
        return result
    except (LicenseError, ServiceError) as error:
        return Entitlement(reason=str(error))


def accept_status(code: str, serial: str, receipt: Any, nonce: str) -> dict[str, Any]:
    """配置请求的可信状态；只更新仍选中本次原码的本地记录。"""
    data = verify_receipt(code, serial, receipt, nonce=nonce)
    if code_hash(load_code() or "") == code_hash(code):
        if not save_activation(code, receipt):
            raise LicenseError("可信授权状态保存失败")
        from . import refresh_entitlement
        refresh_entitlement()
    return data
