"""端到端加密契约：确定性密文、跨端同参派生、边界加解密。"""

from lvjiang.core.account import e2e


def test_derive_key_deterministic_and_password_sensitive():
    a1 = e2e.derive_key("pw-123456", "a" * 32)
    a2 = e2e.derive_key("pw-123456", "a" * 32)
    b = e2e.derive_key("pw-654321", "a" * 32)
    c = e2e.derive_key("pw-123456", "b" * 32)
    assert a1 == a2                        # 同口令同账号 → 同钥（三端一致）
    assert a1 != b and a1 != c             # 换口令/换账号 → 不同钥
    assert len(a1) == 32


def test_encrypt_json_roundtrip_and_deterministic():
    key = e2e.derive_key("pw-123456", "a" * 32)
    data = {"attributes": {"实名": "张三"}, "avatar": "a.png"}
    wrapped1 = e2e.encrypt_json(key, data)
    wrapped2 = e2e.encrypt_json(key, data)
    assert wrapped1 == wrapped2            # 合成 IV：同明文同密文（sha/签名机制依赖）
    assert e2e.is_encrypted(wrapped1)
    assert e2e.decrypt_json(key, wrapped1) == data
    # 错钥解不开；非封装（legacy 明文）返回 None
    assert e2e.decrypt_json(e2e.derive_key("other", "a" * 32), wrapped1) is None
    assert e2e.decrypt_json(key, {"attributes": {}}) is None


def test_encrypt_text_roundtrip():
    key = e2e.derive_key("pw-123456", "a" * 32)
    text = '{"notes": [{"text": "重要信息", "updated_at": "t"}]}'
    wrapped = e2e.encrypt_text(key, text)
    assert e2e.is_encrypted_text(wrapped)
    assert e2e.decrypt_text(key, wrapped) == text
    assert e2e.decrypt_text(key, "普通明文") is None       # legacy 原文不误伤
    assert not e2e.is_encrypted_text("普通明文")


def test_wrong_key_fails_closed_not_garbage(tmp_path):
    """改密场景的安全核心：错钥解密**必然失败**而不是产出错误明文。

    GCM 认证标签保证旧钥密文对新钥只有 InvalidTag 一种结果——不存在
    「错误解密出来的数据」覆盖本地正确数据的可能；失败后封装结构原样
    保留，拉取边界据此跳过、保留本地文件。
    """
    old_key = e2e.derive_key("pw-old", "a" * 32)
    new_key = e2e.derive_key("pw-new", "a" * 32)
    wrapper = e2e.encrypt_json(old_key, {"attributes": {"实名": "张三"}})
    # 错钥：返回 None（调用方跳过），绝不返回看起来合理的字典
    assert e2e.decrypt_json(new_key, wrapper) is None
    # 封装结构原样保留（拉取边界据此识别「这是密文，跳过写入」）
    assert e2e.is_encrypted(wrapper)
    wrapped_text = e2e.encrypt_text(old_key, "重要便利贴内容")
    assert e2e.decrypt_text(new_key, wrapped_text) is None
    assert e2e.is_encrypted_text(wrapped_text)


def test_encrypt_text_uses_z1_and_reads_legacy_uncompressed():
    import json

    key = e2e.derive_key("pw-123456", "a" * 32)
    text = '{"plans": {"p1": {"name": "大量重复结构"}}}' * 10
    wrapped_z1 = e2e.encrypt_text(key, text)
    parsed = json.loads(wrapped_z1)
    assert parsed["e2e"] == 1 and parsed["z"] == 1
    assert len(wrapped_z1) < len(text)
    assert e2e.decrypt_text(key, wrapped_z1) == text

    # 兼容无 z 旧密文
    legacy_wrapped = json.dumps(e2e._encrypt(key, text.encode("utf-8"), compress=False))
    assert "z" not in json.loads(legacy_wrapped)
    assert e2e.decrypt_text(key, legacy_wrapped) == text


def test_encrypt_text_deterministic_across_time_and_os_header(monkeypatch):
    import gzip
    import time

    clock = iter([1_700_000_000.0, 1_700_000_100.0, 1_700_000_200.0])
    monkeypatch.setattr(time, "time", lambda: next(clock))

    key = e2e.derive_key("pw-123456", "a" * 32)
    text = '{"notes": [{"text": "跨秒确定性验证", "updated_at": "t"}]}'
    wrapped1 = e2e.encrypt_text(key, text)
    wrapped2 = e2e.encrypt_text(key, text)
    assert wrapped1 == wrapped2
    assert e2e.decrypt_text(key, wrapped1) == text

    gz = e2e._deterministic_gzip(b"hello world")
    assert gz[:10] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    assert gzip.decompress(gz) == b"hello world"


def test_http_426_client_upgrade_required_keeps_session():
    import io
    from urllib import error as urlerror

    from lvjiang.core.account import service, transport

    exc = urlerror.HTTPError(
        "http://test.local/api/sync/manifest",
        426,
        "Upgrade Required",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"client_upgrade_required"}'),
    )
    err = transport.http_error(exc, messages=service.ERROR_MESSAGES, rejected=service.AUTH_REJECTED_CODES)
    assert err.code == "client_upgrade_required"
    assert err.code not in service.AUTH_REJECTED_CODES
    assert "升级" in str(err)
