"""基础属性与便利贴的端到端加密 —— 同步边界加解密，本地文件保持明文。

威胁模型：云端 D1 只应见到密文。密钥在客户端登录瞬间由**登录口令**派生
（PBKDF2-HMAC-SHA256，210k 迭代，盐 = 固定前缀 + account_id），三端各自
派生出同一把钥；口令与密钥都不上云（本地 account.json 缓存密钥仅用于
restore 免重派生——本地磁盘上本就是明文数据，缓存不降低安全性）。

算法：AES-256-GCM，nonce 为**合成 IV**（SHA-256(key‖"iv"‖明文)[:12]，
SIV 构造）：同明文得同密文——文档通道的 sha/CAS 与名册的内容签名机制
完全照常工作；不同明文的 nonce 碰撞可忽略。改口令即换钥：旧密文由
下一次同步自动整体重推（密文签名必变，触发上传）。

封装格式：{"e2e": 1, "n": <b64 nonce>, "c": <b64 ct+tag>}。读取端见到
非封装内容按 legacy 明文处理（过渡期读旧数据，写入时即加密上推）。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: 派生盐前缀：版本化，将来换算法/参数时递增
_SALT_PREFIX = b"lvjiang-e2e-v1:"
#: PBKDF2 迭代次数（与网页端 WebCrypto 参数一致，改一处必须同步另一处）
PBKDF2_ITERATIONS = 210_000

# 进程内密钥：AccountSession 登录/恢复时 set，同步通道从这里取
_KEY: bytes | None = None


def derive_key(password: str, account_id: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _SALT_PREFIX + account_id.encode("ascii"),
        PBKDF2_ITERATIONS, dklen=32)


def set_key(key: bytes | None) -> None:
    global _KEY
    _KEY = key


def current_key() -> bytes | None:
    return _KEY


def key_fingerprint(key: bytes) -> str:
    """派生密钥的短摘要（用于本地追踪密文化迁移基线，不存明文口令）。"""
    return hashlib.sha256(key).hexdigest()[:16]


# ─── account.json 密钥缓存（restore 时免去 210k 派生） ─────────────────

def persist_key_into(key: bytes, profile: dict) -> None:
    """把派生钥写进调用方即将保存的 account.json 字典（避免二次读写）。"""
    profile["e2e_key"] = key.hex()


def persist_key(key: bytes) -> None:
    """独立写回 account.json（改密换钥等不由 login 主导的路径）。"""
    from ..account.service import mutate_profile

    def _set(profile: dict) -> None:
        persist_key_into(key, profile)

    mutate_profile(_set)


def forget_key() -> None:
    """丢弃进程内密钥并抹掉 account.json 里的 hex 缓存。

    登出 / 会话被服务端拒绝 / 换号时调用：密钥是口令的 PBKDF2 派生（等价于口令
    的离线可验证副本），不该在「已经没有登录态」之后继续留在内存与磁盘上。
    """
    from ..account.service import load_profile, mutate_profile

    set_key(None)
    if "e2e_key" not in load_profile():
        return

    def _drop(profile: dict) -> None:
        profile.pop("e2e_key", None)

    mutate_profile(_drop)


def load_stored_key() -> bytes | None:
    from ..account.service import load_profile

    hex_value = load_profile().get("e2e_key")
    if isinstance(hex_value, str) and len(hex_value) == 64:
        try:
            return bytes.fromhex(hex_value)
        except ValueError:
            return None
    return None


# ─── 加解密 ────────────────────────────────────────────────────

def _synthetic_nonce(key: bytes, plaintext: bytes) -> bytes:
    return hashlib.sha256(key + b"iv" + plaintext).digest()[:12]


def _deterministic_gzip(data: bytes) -> bytes:
    # 固定 RFC 1952 头（mtime=0, XFL=2, OS=255 unknown）：不能直接用
    # gzip.compress(..., mtime=0)，Python 3.12+ 该分支走 zlib.compress(wbits=31)，
    # 会按平台写 OS 字节（Windows 0x0a / Linux 0x03），破坏跨端密文一致。
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    compressed = zlib.compress(data, level=9, wbits=-15)
    trailer = struct.pack("<LL", zlib.crc32(data) & 0xFFFFFFFF, len(data) & 0xFFFFFFFF)
    return header + compressed + trailer


def _encrypt(key: bytes, plaintext: bytes, *, compress: bool = False) -> dict:
    payload = _deterministic_gzip(plaintext) if compress else plaintext
    nonce = _synthetic_nonce(key, payload)
    ct = AESGCM(key).encrypt(nonce, payload, None)
    wrapper: dict[str, object] = {
        "e2e": 1,
        "n": base64.b64encode(nonce).decode("ascii"),
        "c": base64.b64encode(ct).decode("ascii"),
    }
    if compress:
        wrapper["z"] = 1
    return wrapper


def _decrypt(key: bytes, wrapper: dict) -> bytes | None:
    try:
        nonce = base64.b64decode(wrapper["n"])
        ct = base64.b64decode(wrapper["c"])
        raw = AESGCM(key).decrypt(nonce, ct, None)
        if wrapper.get("z") == 1:
            return gzip.decompress(raw)
        return raw
    except Exception:   # noqa: BLE001 — 错钥 InvalidTag / 解压损坏都按解不开处理
        return None


def is_encrypted(value) -> bool:
    return (isinstance(value, dict) and value.get("e2e") == 1
            and value.get("z") in (None, 0, 1)
            and isinstance(value.get("n"), str) and isinstance(value.get("c"), str))


def encrypt_json(key: bytes, data: dict) -> dict:
    """对象 → 封装对象（名册 data 字段：服务端按对象透传）。"""
    return _encrypt(key, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def decrypt_json(key: bytes, wrapper) -> dict | None:
    """封装对象 → 原对象；非封装（legacy 明文）或解密失败返回 None。"""
    if not is_encrypted(wrapper):
        return None
    raw = _decrypt(key, wrapper)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_encrypted_text(text: str) -> bool:
    """封装 JSON 字符串形态的密文判定（便利贴文档）。"""
    try:
        return is_encrypted(json.loads(text))
    except ValueError:
        return False


def encrypt_text(key: bytes, text: str) -> str:
    """文本 → 先 gzip 压缩再加密的封装 JSON 字符串（z:1 兼容无 z 旧密文）。"""
    return json.dumps(_encrypt(key, text.encode("utf-8"), compress=True), ensure_ascii=False)


def decrypt_text(key: bytes, wrapped: str) -> str | None:
    """封装 JSON 字符串 → 原文本；非封装或失败返回 None。"""
    try:
        wrapper = json.loads(wrapped)
    except ValueError:
        return None
    if not is_encrypted(wrapper):
        return None
    raw = _decrypt(key, wrapper)
    return raw.decode("utf-8") if raw is not None else None
