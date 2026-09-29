"""高级功能离线授权：序列号派生、激活码验签、绑定与有效期

这套东西的价值在于「别人拿不到的码用不了」，所以测试重点是**拒绝**路径：改一个
字节、换一把钥匙、过了期、换台机器，都必须不通过。放行路径反而只是陪衬。

不测「能不能被逆向绕过」——客户端校验天然可以被绕过，那不是这层的职责。
"""

from datetime import date

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lvjiang.core.license import code as code_mod
from lvjiang.core.license import hardware, store
from lvjiang.core.license.code import (
    LicenseError,
    build_payload,
    evaluate,
    format_code,
    verify_code,
)

_SERIAL = None  # 由 fixture 填充成一个自洽的序列号


def _b32(raw: bytes) -> str:
    import base64
    return base64.b32encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def signer():
    """一对临时密钥：签发端与验签端"""
    private = Ed25519PrivateKey.generate()
    public_b32 = _b32(private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))

    def issue(**kwargs) -> str:
        payload = build_payload(**kwargs)
        return format_code(payload, private.sign(payload))

    return issue, public_b32


@pytest.fixture
def serial():
    return hardware.encode_serial("00000000-1111-2222-3333-444444444444")


class TestSerial:
    def test_same_hardware_gives_same_serial(self):
        a = hardware.encode_serial("abc-def")
        b = hardware.encode_serial("abc-def")
        assert a == b

    def test_different_hardware_gives_different_serial(self):
        assert hardware.encode_serial("abc") != hardware.encode_serial("abd")

    def test_serial_does_not_leak_raw_hardware_id(self, serial):
        """展示值必须是哈希后的：原始硬件 UUID 不该出现在界面或日志里。"""
        assert "00000000-1111" not in serial
        assert "4444" not in hardware.normalize_serial(serial)

    def test_check_digit_catches_typo(self, serial):
        assert hardware.serial_is_wellformed(serial)
        body = hardware.normalize_serial(serial)
        # 改一位，校验位就对不上——用户抄错时当场能发现
        wrong = ("B" if body[0] != "B" else "C") + body[1:]
        assert not hardware.serial_is_wellformed(wrong)

    def test_grouping_is_ignored_when_comparing(self, serial):
        assert hardware.normalize_serial(serial) == \
            hardware.normalize_serial(serial.replace("-", " ").lower())


class TestSmbiosParsing:
    """固件表解析：真机拿不到就没法验，所以喂构造好的字节流"""

    @staticmethod
    def _table(uuid_bytes: bytes) -> bytes:
        header = b"\x00" * 8                     # RawSMBIOSData 头
        body = bytes([1, 27, 0, 0]) + b"\x00" * 4 + uuid_bytes + b"\x00" * 3
        return header + body + b"\x00\x00"       # 结构 + 空字符串区

    def test_reads_type1_uuid(self):
        raw = bytes(range(16))
        value = hardware.parse_smbios_uuid(self._table(raw))
        assert value is not None
        # 前三段按小端存放，解析必须用 bytes_le，否则整段顺序都是错的
        assert value.startswith("03020100-0504-0706")

    def test_rejects_all_zero_uuid(self):
        """部分虚拟机/OEM 把 UUID 填成全 0，不能当成有效标识。"""
        assert hardware.parse_smbios_uuid(self._table(b"\x00" * 16)) is None

    def test_rejects_all_ff_uuid(self):
        assert hardware.parse_smbios_uuid(self._table(b"\xff" * 16)) is None

    def test_truncated_table_returns_none(self):
        assert hardware.parse_smbios_uuid(b"\x00" * 4) is None
        assert hardware.parse_smbios_uuid(b"") is None


class TestVerify:
    def test_roundtrip_bound_code(self, signer, serial):
        issue, public = signer
        code = issue(code_id="L1", bind="serial", features=["bg_capture"],
                     serial=hardware.normalize_serial(serial))
        license_ = verify_code(code, public)
        assert license_.code_id == "L1"
        assert license_.is_bound
        assert license_.has("bg_capture")

    def test_roundtrip_unbound_code(self, signer):
        issue, public = signer
        code = issue(code_id="L2", bind="none", features=["bg_capture"])
        license_ = verify_code(code, public)
        assert not license_.is_bound
        assert license_.serial is None

    def test_tampered_payload_is_rejected(self, signer, serial):
        """改正文必须失败——否则谁都能把自己的码改成全功能。"""
        issue, public = signer
        code = issue(code_id="L1", bind="serial", features=["bg_capture"],
                     serial=hardware.normalize_serial(serial))
        head, payload, sig = code.split(".")
        flipped = ("A" if payload[5] != "A" else "B")
        tampered = f"{head}.{payload[:5]}{flipped}{payload[6:]}.{sig}"
        assert tampered != code
        with pytest.raises(LicenseError):
            verify_code(tampered, public)

    def test_tampered_signature_is_rejected(self, signer):
        issue, public = signer
        code = issue(code_id="L2", bind="none", features=["x"])
        head, payload, sig = code.split(".")
        flipped = ("A" if sig[0] != "A" else "B")
        with pytest.raises(LicenseError):
            verify_code(f"{head}.{payload}.{flipped}{sig[1:]}", public)

    def test_code_from_another_key_is_rejected(self, signer):
        """换一把私钥签的码不能通过——否则任何人都能自己发码。"""
        issue, _ = signer
        code = issue(code_id="L3", bind="none", features=["x"])
        other = Ed25519PrivateKey.generate()
        other_pub = _b32(other.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ))
        with pytest.raises(LicenseError):
            verify_code(code, other_pub)

    def test_garbage_is_rejected_without_crashing(self, signer):
        _, public = signer
        for text in ["", "hello", "LVJ1.xx", "LVJ1.!!!.???", "LVJ9.AA.BB"]:
            with pytest.raises(LicenseError):
                verify_code(text, public)

    def test_missing_public_key_rejects_everything(self, signer):
        """没配公钥时一律不通过，而不是崩溃或默认放行。"""
        issue, _ = signer
        with pytest.raises(LicenseError):
            verify_code(issue(code_id="L", bind="none", features=[]), "")

    def test_whitespace_and_dashes_are_tolerated(self, signer):
        """用户从聊天记录里粘来的内容常带换行和连字符。"""
        issue, public = signer
        code = issue(code_id="L4", bind="none", features=["x"])
        messy = code[:40] + "\n  " + code[40:80] + "-" + code[80:]
        assert verify_code(messy, public).code_id == "L4"

    def test_unknown_bind_mode_is_rejected(self, signer):
        """显式枚举：不认识的绑定模式一律拒绝，不猜。"""
        import json
        payload = json.dumps({"v": 1, "id": "L", "bind": "whatever",
                              "feat": []}, separators=(",", ":"),
                             sort_keys=True).encode()
        private = Ed25519PrivateKey.generate()
        # 用能通过签名校验的方式构造，确保失败原因是 bind 而不是签名
        pub = _b32(private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw))
        code = format_code(payload, private.sign(payload))
        with pytest.raises(LicenseError, match="绑定模式"):
            verify_code(code, pub)


class TestEvaluate:
    """把「一张码 + 本机序列号」判成最终结论"""

    def _patch_key(self, monkeypatch, public):
        monkeypatch.setattr(code_mod, "PUBLIC_KEY_B32", public)

    def test_unbound_code_works_without_serial(self, signer, monkeypatch):
        """免绑定码不看序列号：读不到硬件标识也能激活。"""
        issue, public = signer
        self._patch_key(monkeypatch, public)
        result = evaluate(issue(code_id="L", bind="none",
                                features=["bg_capture"]), None)
        assert result.active
        assert result.has("bg_capture")

    def test_bound_code_requires_matching_serial(self, signer, monkeypatch,
                                                 serial):
        issue, public = signer
        self._patch_key(monkeypatch, public)
        code = issue(code_id="L", bind="serial", features=["bg_capture"],
                     serial=hardware.normalize_serial(serial))
        assert evaluate(code, serial).active

    def test_bound_code_on_another_machine_is_rejected(self, signer,
                                                       monkeypatch, serial):
        """绑机码转发给别人必须无效——这是整套方案的主要价值。"""
        issue, public = signer
        self._patch_key(monkeypatch, public)
        code = issue(code_id="L", bind="serial", features=["bg_capture"],
                     serial=hardware.normalize_serial(serial))
        other = hardware.encode_serial("99999999-9999-9999-9999-999999999999")
        result = evaluate(code, other)
        assert not result.active
        assert "不是本机" in result.reason

    def test_bound_code_without_serial_is_rejected(self, signer, monkeypatch,
                                                   serial):
        issue, public = signer
        self._patch_key(monkeypatch, public)
        code = issue(code_id="L", bind="serial", features=["x"],
                     serial=hardware.normalize_serial(serial))
        result = evaluate(code, None)
        assert not result.active

    def test_expired_code_is_rejected(self, signer, monkeypatch):
        issue, public = signer
        self._patch_key(monkeypatch, public)
        code = issue(code_id="L", bind="none", features=["x"],
                     expires=date(2020, 1, 1))
        result = evaluate(code, None, today=date(2020, 1, 2))
        assert not result.active
        assert "过期" in result.reason

    def test_code_valid_on_its_last_day(self, signer, monkeypatch):
        issue, public = signer
        self._patch_key(monkeypatch, public)
        code = issue(code_id="L", bind="none", features=["x"],
                     expires=date(2020, 1, 1))
        assert evaluate(code, None, today=date(2020, 1, 1)).active

    def test_no_code_means_no_features(self, signer, monkeypatch):
        _, public = signer
        self._patch_key(monkeypatch, public)
        result = evaluate(None, None)
        assert not result.active
        assert not result.has("bg_capture")

    def test_feature_not_in_code_stays_off(self, signer, monkeypatch):
        """只开签发时写明的功能，不因为「激活了」就全开。"""
        issue, public = signer
        self._patch_key(monkeypatch, public)
        result = evaluate(issue(code_id="L", bind="none",
                                features=["other_feature"]), None)
        assert result.active
        assert not result.has("bg_capture")


class TestStore:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.core.config.resolver.LOCAL_CONFIG_DIR", tmp_path)
        assert store.load_code() is None
        assert store.save_code("LVJ1.AAA.BBB")
        assert store.load_code() == "LVJ1.AAA.BBB"
        assert store.clear_code()
        assert store.load_code() is None

    def test_lives_outside_the_upgrade_wiped_dir(self, monkeypatch, tmp_path):
        """必须落在 config/local：config/system 升级时会被整个清空。"""
        monkeypatch.setattr(
            "lvjiang.core.config.resolver.LOCAL_CONFIG_DIR", tmp_path / "local")
        assert "local" in store.license_path().parts

    def test_missing_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.core.config.resolver.LOCAL_CONFIG_DIR", tmp_path / "nope")
        assert store.load_code() is None


def test_builder_rejects_bound_code_without_serial():
    with pytest.raises(ValueError):
        build_payload(code_id="L", bind="serial", features=[])


def test_builder_rejects_unknown_bind():
    with pytest.raises(ValueError):
        build_payload(code_id="L", bind="maybe", features=[])
