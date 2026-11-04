"""设备身份的两个层次 —— 机器级与实例级，用途不同、绝不可混用。

一台电脑上可能同时存在多个**数据彼此独立**的实例（源码版、安装版、便携副本），
它们共享同一块硬件，但各自的 ``config/session`` 是不同的一份数据。

| 标识 | 派生 | 同机多实例 | 用途 |
| --- | --- | --- | --- |
| ``machine_device_id`` | 仅机器指纹 | **完全相同** | 激活码绑定、账本「哪台机器写的」。一台机器一个授权，不因实例而变 |
| ``instance_id`` | 机器指纹 + 实例 uuid | **各不相同** | 账号登录设备、会话、同步幂等键。两个实例各自的登录态与 history 必须分开 |

混用会立刻出事：把实例维度混进机器标识，同机换个版本就变成「另一台机器」，激活
失效；反过来把机器标识用于账号设备，源码版和安装版会合成一条设备记录、共用一个
会话、互顶登录态。

两个标识都**每次现算、不落盘**（同 ``license.hardware`` 的原则）：存下来等于给了
一个可以直接改的文件，而且换机器后会残留上一台的值。唯一落盘的是随机的实例 uuid。

``keyring`` 的凭据键也必须带实例维度：Windows 凭据管理器是机器级共享的，键里不带
实例标识时，源码版登录一次，安装版启动就能读到同一个 refresh 令牌。
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from loguru import logger

from ..license.hardware import machine_fingerprint

#: 机器级标识派生盐。换值等于把每台机器都变成「新机器」，会让激活绑定与账本归属
#: 全部失配——改动需谨慎。
_MACHINE_SALT = b"lvjiang-machine-device-v1"

#: 实例级标识派生盐。换值等于把所有实例都变成「新设备」，服务端重建设备记录。
_INSTANCE_SALT = b"lvjiang-device-instance-v1"


def new_instance_uuid() -> str:
    """生成新的实例标识（32 位十六进制，与 install_id 同形态）。"""
    return uuid.uuid4().hex


def _machine_component() -> str:
    """机器部分；取不到硬件标识时退化为空串。"""
    fingerprint = machine_fingerprint()
    if fingerprint is None:
        logger.warning("设备标识取不到机器指纹，仅按实例标识派生")
        return ""
    return f"{fingerprint[0]}:{fingerprint[1]}"


def machine_device_id() -> str:
    """机器级设备标识：**同机所有实例完全一致**，供激活码绑定使用。"""
    return hashlib.sha256(
        _MACHINE_SALT + _machine_component().encode("utf-8")).hexdigest()[:32]


def instance_id(instance_uuid: str) -> str:
    """实例级标识：机器指纹 + 实例 uuid，供账号登录与同步使用。"""
    if not instance_uuid:
        raise ValueError("实例标识不能为空")
    material = "\n".join((_machine_component(), instance_uuid))
    return hashlib.sha256(_INSTANCE_SALT + material.encode("utf-8")).hexdigest()[:32]


def keyring_user(instance_uuid: str) -> str:
    """凭据库里的实例隔离键；无实例标识时返回空串（调用方据此判定无登录态）。"""
    if not instance_uuid:
        return ""
    return f"session:{instance_uuid}"


def device_author_id() -> str:
    """账本行作者标记：**实例出生标识**（见 :func:`stable_instance_id`）。

    「每端只上传自己写的行」的依据：离线包复制来的行带着来源实例的标记，
    接收方永不代传。旧存量行（空标记）视为出处不明，不上传。
    """
    return stable_instance_id()


_stable_instance_id_cache: str | None = None


def _instance_id_path() -> Path:
    """实例出生文件位置：``config/session/instance.id``。

    config/session 是运行态目录——随客户端整体压缩/解压/移动，身份跟着
    走；删 account.json 等账号态文件不影响它。不做任何旧位置迁移：文件
    由部署时直接摆放，代码只认这一个路径。
    """
    from ...constants import SESSION_CONFIG_DIR

    return SESSION_CONFIG_DIR / "instance.id"


def stable_instance_id() -> str:
    """实例出生标识：首次调用生成随机 uuid 并落盘，此后**永不变化**。

    这是实例的唯一身份，用于账号设备登记与账本行作者标记。刻意不做任何
    派生（不掺机器指纹、不 hash 目录/包名）：一台机器可能装多个实例、
    实例目录可能被移动，任何派生都会导致「身份漂移」——重装/搬迁/删
    account.json 之后自己写的行自己不认。文件损坏或丢失时生成新 id
    （等于一个新实例；旧 id 的行成为出处不明，不上传，安全降级）。
    """
    global _stable_instance_id_cache
    if _stable_instance_id_cache is not None:
        return _stable_instance_id_cache
    path = _instance_id_path()
    try:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                _stable_instance_id_cache = value
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = uuid.uuid4().hex
        path.write_text(value + "\n", encoding="utf-8")
        _stable_instance_id_cache = value
        return value
    except OSError as err:
        logger.warning("实例出生标识读写失败，本次进程内使用临时标识：{}", err)
        _stable_instance_id_cache = uuid.uuid4().hex
        return _stable_instance_id_cache
