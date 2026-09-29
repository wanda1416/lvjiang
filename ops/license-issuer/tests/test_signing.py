"""签发逻辑测试（不碰界面）

重点是**签出来的码客户端一定收得下**：签发端和验签端一旦格式走偏，现象是用户说
「激活码无效」，从两头都看不出问题。所以这里每签一张都拿主程序的验签函数验回去。
"""

import base64
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from license_issuer.signing import (
    IssueRequest,
    SigningKeyError,
    issue,
    key_status,
)


@pytest.fixture
def key_file(tmp_path) -> Path:
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "key.txt"
    path.write_text(base64.b32encode(raw).decode().rstrip("=") + "\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def public_b32(key_file) -> str:
    text = key_file.read_text(encoding="utf-8").strip()
    raw = base64.b32decode(text + "=" * (-len(text) % 8))
    private = Ed25519PrivateKey.from_private_bytes(raw)
    return base64.b32encode(private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)).decode().rstrip("=")


def _serial() -> str:
    from lvjiang.core.license.hardware import encode_serial
    return encode_serial("test-machine")


class TestKeyStatus:
    def test_missing_file(self, tmp_path):
        ok, message = key_status(tmp_path / "nope.txt")
        assert not ok
        assert "找不到私钥" in message

    def test_garbage_file(self, tmp_path):
        path = tmp_path / "bad.txt"
        path.write_text("这不是密钥", encoding="utf-8")
        ok, message = key_status(path)
        assert not ok
        assert "无效" in message

    def test_good_key(self, key_file):
        ok, _ = key_status(key_file)
        assert ok


class TestIssue:
    def test_bound_code_verifies(self, key_file, public_b32):
        from lvjiang.core.license.code import verify_code
        code = issue(IssueRequest(
            code_id="L1", bind_to_serial=True, serial=_serial(),
            features=("lv1",)), key_file)
        license_ = verify_code(code, public_b32)
        assert license_.is_bound
        assert license_.code_id == "L1"

    def test_unbound_code_verifies(self, key_file, public_b32):
        from lvjiang.core.license.code import verify_code
        code = issue(IssueRequest(
            code_id="L2", bind_to_serial=False,
            features=("lv1",), expires=date(2030, 1, 1)), key_file)
        license_ = verify_code(code, public_b32)
        assert not license_.is_bound
        assert license_.expires == date(2030, 1, 1)

    def test_serial_grouping_is_normalized(self, key_file, public_b32):
        """用户复制过来的序列号带分组符，签发端要归一化后再写进正文。"""
        from lvjiang.core.license.code import verify_code
        from lvjiang.core.license.hardware import normalize_serial
        code = issue(IssueRequest(
            code_id="L3", bind_to_serial=True,
            serial=_serial().lower().replace("-", " "),
            features=("lv1",)), key_file)
        assert verify_code(code, public_b32).serial == normalize_serial(_serial())

    def test_missing_key_is_reported(self, tmp_path):
        with pytest.raises(SigningKeyError):
            issue(IssueRequest(code_id="L", bind_to_serial=False,
                               features=("lv1",), expires=date(2030, 1, 1)),
                  tmp_path / "nope")


class TestValidation:
    def test_requires_code_id(self):
        assert "编号" in IssueRequest(
            code_id="  ", bind_to_serial=False, features=("lv1",),
            expires=date(2030, 1, 1)).validate()

    def test_requires_a_level(self):
        """没有等级的码签出来毫无意义，拦在签发前。"""
        assert "等级" in IssueRequest(
            code_id="L", bind_to_serial=False,
            expires=date(2030, 1, 1)).validate()

    def test_rejects_unregistered_level(self):
        """手打的等级名客户端认不出来，签出来也开不了东西——签发前就拦住。"""
        problem = IssueRequest(
            code_id="L", bind_to_serial=False, features=("lv9",),
            expires=date(2030, 1, 1)).validate()
        assert "未登记" in problem

    def test_unbound_requires_expiry(self):
        """免绑定码是 bearer token，离线又撤不掉，必须有有效期。"""
        assert "有效期" in IssueRequest(
            code_id="L", bind_to_serial=False, features=("lv1",)).validate()

    def test_bound_may_be_permanent(self):
        """绑机码只在那台机器上生效，永久是可接受的。"""
        assert IssueRequest(
            code_id="L", bind_to_serial=True, serial=_serial(),
            features=("lv1",)).validate() == ""

    def test_bound_requires_serial(self):
        assert "序列号" in IssueRequest(
            code_id="L", bind_to_serial=True, features=("lv1",)).validate()

    def test_bound_rejects_typoed_serial(self):
        """校验位不对就别签了——签出来用户也用不了，白跑一轮沟通。"""
        bad = _serial()[:-1] + ("A" if _serial()[-1] != "A" else "B")
        assert "校验位" in IssueRequest(
            code_id="L", bind_to_serial=True, serial=bad,
            features=("lv1",)).validate()

    def test_unbound_ignores_serial(self):
        assert IssueRequest(
            code_id="L", bind_to_serial=False, serial="garbage",
            features=("lv1",), expires=date(2030, 1, 1)).validate() == ""
