"""首次在线证明不可伪造、同机恢复及离线记录的设备/到期边界。"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lvjiang.core import license as licensing
from lvjiang.core.license import activation, code, hardware, store
from lvjiang.core.paths import CONFIG_SUBDIRECTORIES


def b32(raw):
    return base64.b32encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def grant(tmp_path, monkeypatch):
    issuer, server = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    for name, private in (("PUBLIC_KEY_B32", issuer), ("ACTIVATION_PUBLIC_KEY_B32", server)):
        monkeypatch.setattr(code, name, b32(private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)))
    monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "local", tmp_path)
    serial = hardware.encode_serial("test-activation-machine")
    monkeypatch.setattr(licensing, "current_serial", lambda: serial)
    monkeypatch.setattr(licensing, "_cached", None)
    payload = code.build_payload("test-claim", "claim", ["lv1"], days=2)
    voucher = code.format_code(payload, issuer.sign(payload))
    activated = int(time.time())
    def receipt(nonce="a" * 32, status="active", **changes):
        data = {"v": 1, "purpose": "license_activation_response", "code_hash": activation.code_hash(voucher),
                "code_id": "test-claim", "serial": hardware.normalize_serial(serial), "nonce": nonce,
                "server_time": activated, "status": status}
        if status == "active":
            data.update(features=["lv1"], days=2, activated_at=activated, expires_at=activated + 2 * 86400)
        data.update(changes)
        raw = json.dumps(data).encode("utf-8")
        return {"payload": b32(raw), "signature": b32(server.sign(raw))}
    return voucher, serial, receipt, activated


def test_first_activation_requires_fresh_signed_reply_and_save_failure_preserves_old(grant, monkeypatch):
    voucher, serial, receipt, _ = grant
    assert not code.evaluate(voucher, serial).active
    monkeypatch.setattr("lvjiang.core.config.content_service.request_json",
                        lambda base, route, data: receipt(nonce="b" * 32))
    assert not licensing.try_activate(voucher).active
    assert store.load_code() is None
    def respond(base, route, data):
        assert route == "/v1/activate"
        assert data["code"] == voucher
        return receipt(nonce=data["nonce"])
    monkeypatch.setattr("lvjiang.core.config.content_service.request_json", respond)
    assert licensing.try_activate("\x1c" + voucher + "\x85").active
    assert licensing.refresh_entitlement().has("lv1")
    original = store.activation_path().read_bytes()
    monkeypatch.setattr(activation, "save_activation", lambda *args: False)
    assert not licensing.try_activate(voucher).active
    assert store.activation_path().read_bytes() == original
    assert licensing.current_entitlement().active


def test_receipt_rejects_tampering_wrong_device_expiry_and_missing_original(grant):
    voucher, serial, receipt, activated = grant
    valid = receipt()
    activation.verify_receipt(voucher, serial, valid, now=activated + 2 * 86400 - 1)
    with pytest.raises(code.LicenseError):
        activation.verify_receipt(voucher, serial, valid, now=activated + 2 * 86400)
    other = hardware.encode_serial("different-test-machine")
    assert not activation.receipt_entitlement(voucher, other, valid).active
    valid["signature"] = b32(b"\x00" * 64)
    assert not activation.receipt_entitlement(voucher, serial, valid).active
    assert not activation.receipt_entitlement("", serial, receipt()).active
    assert not activation.receipt_entitlement(voucher, serial, receipt(days=3)).active
    assert not activation.receipt_entitlement(voucher, serial, receipt(purpose="other")).active


def test_signed_revocation_survives_restart_without_revoking_a_new_selection(grant, monkeypatch):
    voucher, serial, receipt, _ = grant
    assert store.save_activation(voucher, receipt())
    activation.accept_status(voucher, serial, receipt(status="disabled"), "a" * 32)
    assert not licensing.refresh_entitlement().active
    assert not activation.stored_entitlement(serial).active
    with pytest.raises(code.LicenseError):
        activation.accept_status(voucher, serial, {}, "a" * 32)
    store.clear_code()
    assert not store.activation_path().exists()
    activation.accept_status(voucher, serial, receipt(), "a" * 32)
    assert store.load_code() is None  # A late background reply cannot reselect a cleared code.


def test_cached_claim_expires_at_boundary(grant, monkeypatch):
    voucher, _, receipt, activated = grant
    store.save_activation(voucher, receipt())
    assert licensing.refresh_entitlement().active
    monkeypatch.setattr(time, "time", lambda: activated + 2 * 86400)
    assert not licensing.current_entitlement().active


def test_claim_snapshot_and_package_request_verify_signed_denial(grant, monkeypatch):
    from lvjiang.core.config import member_download, protected_remote
    from lvjiang.core.config.content_service import ServiceError
    from lvjiang.core.update_bundle import json_bytes
    voucher, serial, receipt, activated = grant
    store.save_activation(voucher, receipt())
    monkeypatch.setattr(licensing, "current_serial", lambda: serial)
    access = protected_remote.snapshot()
    assert access is not None and access.serial == hardware.normalize_serial(serial)
    assert access.expires_at == activated + 2 * 86400
    package = {"key_id": "activation-test-package", "sha256": "a" * 64, "config_version": 1,
               "min_app_version": "0.15.0", "url": protected_remote.manifest_url().rsplit("/", 1)[0]
               + "/packages/activation-test-package.bin"}
    index = json_bytes({"schema_version": 1, "config_version": 1, "packages": [package]})
    def fetch(*args, **kwargs):
        return index, "", False
    def proxy(*args, **kwargs):
        raise ServiceError("test proxy", 403, code="forbidden", reason="license_disabled")
    monkeypatch.setattr(member_download, "request_json", proxy)
    with pytest.raises(ServiceError):
        member_download.fetch_member_update(access, "0.15.0", fetch=fetch)
    assert licensing.refresh_entitlement().active
    def signed_denial(base, route, body, **kwargs):
        assert body["serial"] == hardware.normalize_serial(serial)
        return {"error": "forbidden", "receipt": receipt(nonce=body["nonce"], status="disabled")}
    monkeypatch.setattr(member_download, "request_json", signed_denial)
    assert member_download.fetch_member_update(access, "0.15.0", fetch=fetch).files == {}
    assert not licensing.refresh_entitlement().active
    assert protected_remote.snapshot() is None


def test_package_key_and_denial_cannot_be_modified_outside_signed_proof(grant, monkeypatch):
    import hashlib

    from lvjiang.core.config import member_download, protected_remote
    from lvjiang.core.config.protected_bundle import digest, encrypt
    from lvjiang.core.update_bundle import json_bytes
    voucher, serial, receipt, activated = grant
    store.save_activation(voucher, receipt())
    key = b"k" * 32
    encrypted = encrypt({"test.bin": b"test"}, key, b"n" * 12)
    package = {"key_id": "test-proof-package", "sha256": digest(encrypted),
               "min_app_version": "0.15.0", "config_version": 1,
               "url": protected_remote.manifest_url().rsplit("/", 1)[0] + "/packages/test-proof-package.bin"}
    index = json_bytes({"schema_version": 1, "config_version": 1, "packages": [package]})
    access = protected_remote.ProtectedAccess("https://service.example.invalid", voucher,
                                               serial=hardware.normalize_serial(serial))
    def fetch(url, **kwargs):
        return (index if url == protected_remote.manifest_url() else encrypted), "", False
    def respond(base, route, body, **kwargs):
        response = {"key": base64.b64encode(key).decode("ascii"), "sha256": package["sha256"],
                    "min_app_version": "0.15.0", "config_version": 1, "expires": None}
        proof = json.dumps([package["key_id"], response["key"], response["sha256"], "0.15.0", 1, None],
                           separators=(",", ":")).encode("utf-8")
        response["receipt"] = receipt(nonce=body["nonce"], package_key_id=package["key_id"],
                                      package_status="allowed", package_grant_hash=hashlib.sha256(proof).hexdigest())
        # A proxy can add an error string, but cannot sign a package denial.
        response["error"] = "forbidden"
        return response
    monkeypatch.setattr(member_download, "request_json", respond)
    result = member_download.fetch_member_update(access, "0.15.0", fetch=fetch)
    assert result.files == {"test.bin": b"test"}
    assert result.metadata["expires_at"] == activated + 2 * 86400
    assert protected_remote.authorized(result.metadata)
    # Metadata has its own boundary check even if the entitlement provider later changes.
    cached = licensing.current_entitlement()
    with monkeypatch.context() as scoped:
        scoped.setattr(licensing, "current_entitlement", lambda: cached)
        scoped.setattr(time, "time", lambda: activated + 2 * 86400)
        assert not protected_remote.authorized(result.metadata)
    def tamper(*args, **kwargs):
        response = respond(*args, **kwargs)
        response["key"] = base64.b64encode(b"x" * 32).decode("ascii")
        return response
    monkeypatch.setattr(member_download, "request_json", tamper)
    with pytest.raises(ValueError, match="服务器签名"):
        member_download.fetch_member_update(access, "0.15.0", fetch=fetch)
    assert licensing.refresh_entitlement().active


def test_rechecking_selected_code_persists_authenticated_revocation(grant, monkeypatch):
    voucher, _, receipt, _ = grant
    store.save_activation(voucher, receipt())
    assert licensing.refresh_entitlement().active
    monkeypatch.setattr("lvjiang.core.config.content_service.request_json",
                        lambda base, route, body: receipt(nonce=body["nonce"], status="disabled"))
    assert not licensing.try_activate(voucher).active
    assert not licensing.current_entitlement().active
    assert not licensing.refresh_entitlement().active


def test_activation_secrets_are_rejected_by_public_and_encrypted_bundle_contracts():
    from lvjiang.core.config.protected_bundle import encrypt
    from lvjiang.core.update_bundle import safe_path
    assert not safe_path("license-activation.json")
    assert not safe_path("nested/LICENSE.TXT")
    assert not safe_path(".license-unfinished")
    with pytest.raises(ValueError, match="不安全"):
        encrypt({"license-activation.json": b"private-proof"}, b"k" * 32, b"n" * 12)
    assert safe_path("future/opaque.bin")  # Unknown configuration remains distributable.


def test_positive_key_status_skips_unchanged_disk_write_but_recovers_revocation(grant, monkeypatch):
    voucher, serial, receipt, _ = grant
    assert store.save_activation(voucher, receipt())
    original = store.activation_path().read_bytes()
    with monkeypatch.context() as scoped:
        scoped.setattr(activation, "save_activation", lambda *args: pytest.fail("unchanged grant must not write"))
        activation.accept_status(voucher, serial, receipt(nonce="b" * 32), "b" * 32)
    assert store.activation_path().read_bytes() == original
    activation.accept_status(voucher, serial, receipt(status="disabled"), "a" * 32)
    assert not licensing.current_entitlement().active
    activation.accept_status(voucher, serial, receipt(), "a" * 32)
    assert licensing.current_entitlement().active


def test_activation_record_replaces_legacy_and_corruption_fails_closed(grant, monkeypatch):
    voucher, _, receipt, _ = grant
    store.save_code("previous-valid-selection")
    with monkeypatch.context() as scoped:
        def failed_write(*args):
            raise OSError("test disk failure")
        scoped.setattr(store, "_atomic_write", failed_write)
        assert not store.save_activation(voucher, receipt())
    assert store.license_path().read_text(encoding="utf-8").strip() == "previous-valid-selection"
    assert store.save_activation(voucher, receipt())
    assert not store.license_path().exists()
    text = store.activation_path().read_text(encoding="utf-8")
    assert text.endswith("\n") and '\n  "code":' in text
    messages = []
    handler = store.logger.add(lambda message: messages.append(str(message)))
    try:
        store.activation_path().write_text("broken", encoding="utf-8")
        assert store.load_code() is None
        assert any("清除激活" in message for message in messages)
    finally:
        store.logger.remove(handler)
