"""机器序列号派生 — 从不变硬件标识算出，不落盘

序列号**不存储**：每次需要时现算。存下来就等于给了一个可以直接改的文件，
而且换机器后还会留着上一台的值。

取值优先级：

1. **SMBIOS 系统 UUID**（主板固件里的那个）。重装系统、换硬盘都不变，换主板才变，
   是这里唯一真正意义上的「硬件标识」。经 ``GetSystemFirmwareTable`` 直接读固件表，
   不起子进程（``wmic`` 在新版 Windows 已移除，PowerShell 起一次要几百毫秒）。
2. **注册表 MachineGuid**。SMBIOS UUID 取不到或明显无效（全 0 / 全 FF，部分虚拟机和
   OEM 会这么填）时兜底。注意它**重装系统会变**，掉到这一档的用户换系统后需要重新
   签发授权码。

对外展示的是哈希截断值，原始硬件 UUID 不出现在界面、日志和任何上报里——这条和
``core/telemetry/identity.py`` 的匿名化边界一致：遥测那边明令禁止任何硬件标识，
授权序列号是另一条通道，两者不得互相污染。
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
import uuid

from loguru import logger

#: 派生盐。换值会让所有已签发的绑定码失效，改动等于一次强制重新激活。
_SALT = b"lvjiang-license-serial-v1"

#: 序列号取哈希前 20 个 base32 字符 + 1 位校验位，展示时每 5 位一组
_SERIAL_BODY_LEN = 20
_GROUP = 5

#: base32 字母表去掉容易看混的 I/L/O/U，只剩 32 个字符里的可读子集
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ0123456789"

#: 明显无效的 SMBIOS UUID：全 0 / 全 FF，部分虚拟机与 OEM 固件这么填
_BOGUS_UUIDS = {
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
}

#: GetSystemFirmwareTable('RSMB')，小端 4 字节签名
_RSMB = 0x52534D42


def _smbios_uuid() -> str | None:
    """从 SMBIOS Type 1（System Information）结构读系统 UUID"""
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        size = kernel32.GetSystemFirmwareTable(_RSMB, 0, None, 0)
        if size <= 0:
            return None
        buf = ctypes.create_string_buffer(size)
        if kernel32.GetSystemFirmwareTable(_RSMB, 0, buf, size) != size:
            return None
    except Exception as exc:  # 固件表不可用（罕见）
        logger.debug(f"读取 SMBIOS 失败: {exc}")
        return None
    return parse_smbios_uuid(buf.raw)


def parse_smbios_uuid(raw: bytes) -> str | None:
    """从 ``GetSystemFirmwareTable('RSMB')`` 的原始返回里取出系统 UUID。

    独立成纯函数是为了可测：真机固件表拿不到就没法验证解析逻辑，而喂一段构造好的
    字节流可以。结构见 SMBIOS 规范——8 字节 RawSMBIOSData 头之后是若干结构，每个
    结构 ``type(1) + length(1) + handle(2) + 正文``，正文后跟双 NUL 结尾的字符串区。
    Type 1 的 UUID 位于正文偏移 8 起的 16 字节。
    """
    if len(raw) < 8:
        return None
    pos = 8  # 跳过 RawSMBIOSData 头
    while pos + 4 <= len(raw):
        struct_type = raw[pos]
        length = raw[pos + 1]
        if length < 4:
            return None
        if struct_type == 1:
            if pos + 8 + 16 > len(raw):
                return None
            field = raw[pos + 8:pos + 8 + 16]
            # SMBIOS 前三段按小端存放，与 uuid.UUID(bytes=...) 的大端解释相反
            value = str(uuid.UUID(bytes_le=field))
            return None if value in _BOGUS_UUIDS else value
        # 跳过正文，再跳过以双 NUL 结尾的字符串区
        pos += length
        while pos + 1 < len(raw) and raw[pos:pos + 2] != b"\x00\x00":
            pos += 1
        pos += 2
    return None


def _machine_guid() -> str | None:
    """注册表 ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``（重装系统会变）"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
    except Exception as exc:
        logger.debug(f"读取 MachineGuid 失败: {exc}")
        return None
    return str(value).strip().lower() or None


def machine_fingerprint() -> tuple[str, str] | None:
    """返回 ``(来源, 原始标识)``；取不到返回 ``None``。

    原始标识**只在进程内使用**，不展示、不落盘、不上报。
    """
    value = _smbios_uuid()
    if value:
        return "smbios", value.lower()
    value = _machine_guid()
    if value:
        logger.info("SMBIOS UUID 不可用，序列号退回 MachineGuid（重装系统会变）")
        return "machine_guid", value
    return None


def _check_char(body: str) -> str:
    """一位校验字符：用户抄错一个字符时当场就能发现，不用等签发完才对不上"""
    total = sum((i + 1) * _ALPHABET.index(c) for i, c in enumerate(body))
    return _ALPHABET[total % len(_ALPHABET)]


def encode_serial(raw: str) -> str:
    """原始硬件标识 → 展示用序列号（哈希截断 + 校验位，每 5 位一组）"""
    digest = hashlib.sha256(_SALT + raw.encode("utf-8")).digest()
    body = "".join(
        _ALPHABET[b % len(_ALPHABET)] for b in digest[:_SERIAL_BODY_LEN]
    )
    full = body + _check_char(body)
    return "-".join(full[i:i + _GROUP] for i in range(0, len(full), _GROUP))


def normalize_serial(text: str) -> str:
    """去掉分组符与空白并转大写，便于比较用户抄来的序列号"""
    return "".join(ch for ch in text.upper() if ch in _ALPHABET)


def serial_is_wellformed(text: str) -> bool:
    """长度与校验位是否自洽（不代表它是本机的）"""
    body = normalize_serial(text)
    if len(body) != _SERIAL_BODY_LEN + 1:
        return False
    return _check_char(body[:_SERIAL_BODY_LEN]) == body[_SERIAL_BODY_LEN]


def current_serial() -> str | None:
    """本机序列号；取不到硬件标识时返回 ``None``（非 Windows 即如此）"""
    fingerprint = machine_fingerprint()
    if fingerprint is None:
        return None
    return encode_serial(fingerprint[1])
