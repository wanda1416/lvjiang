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
