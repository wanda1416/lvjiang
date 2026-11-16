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
    if activation is None:
        return protocol.evaluate(load_code(), serial)
    issued_code = activation.get("code", "")
    receipt = activation.get("receipt")
    try:
        data = verify_receipt(issued_code, serial, receipt)
    except LicenseError as error:
        return Entitlement(reason=str(error))
    if data["status"] == "active":
        return receipt_entitlement(issued_code, serial, receipt)
    return _recheck_status(issued_code, serial, data)


#: 进程内只复核一次。停用/过期会走在线复核，若每次授权判定都发请求，
#: 后台同步的每一轮都会打一次状态接口。
_status_checked = False


def _recheck_status(issued_code: str, serial: str | None, data: dict[str, Any]) -> Entitlement:
    """本地签名回执是**上次检查的快照**，不是终局判决。

    停用可能被撤销、到期可能续期；若快照永远一票否决，用户就只剩"清除激活
    同机重试"一条路，云端重新启用后客户端永远自愈不了。所以非 active 结论
    在进程首次判定时向云端复核一次（``/v1/license/status`` 现签当前状态）；
    复核失败（离线、服务不可达）保留本地结论，不放大权限。
    """
    global _status_checked
    if _status_checked:
        return Entitlement(reason=STATUS_REASON[data["status"]])
    _status_checked = True
    from ..config.content_service import CONTENT_SERVICE_URL, ServiceError, request_json
    nonce = secrets.token_hex(16)
    try:
        receipt = request_json(CONTENT_SERVICE_URL, "/v1/license/status",
                               {"code": normalize_code(issued_code),
                                "serial": normalize_serial(serial or ""), "nonce": nonce})
        accept_status(issued_code, normalize_serial(serial or ""), receipt, nonce)
        result = receipt_entitlement(issued_code, serial, receipt)
        if result.active:
            from loguru import logger
            logger.info("[授权] 云端复核通过：{} 的停用/过期结论已被云端撤销", data.get("code_id", ""))
        return result
    except (LicenseError, ServiceError) as error:
        from loguru import logger
        logger.debug("[授权] 云端状态复核未完成，保留本地结论：{}", error)
        return Entitlement(reason=STATUS_REASON[data["status"]])


def activate_online(code: str, serial: str | None) -> Entitlement:
    from ..config.content_service import CONTENT_SERVICE_URL, ServiceError, request_json
    nonce = secrets.token_hex(16)
    if not serial or not serial_is_wellformed(serial):
        return Entitlement(reason="读不到有效的本机序列号，无法在线绑定")
    try:
        receipt = request_json(CONTENT_SERVICE_URL, "/v1/activate",
                               {"code": normalize_code(code), "serial": normalize_serial(serial), "nonce": nonce})
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
        stored = load_activation() or {}
        try:
            previous = verify_receipt(code, serial, stored.get("receipt"))
        except LicenseError:
            previous = {}
        fields = ("status", "features", "days", "activated_at", "expires_at")
        if all(previous.get(key) == data.get(key) for key in fields):
            return data
        if not save_activation(code, receipt):
            raise LicenseError("可信授权状态保存失败")
        from . import refresh_entitlement
        refresh_entitlement()
    return data
