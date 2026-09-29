"""高级功能授权 — 离线激活，无服务器

一句话流程：**本机序列号（现算，不存）→ 你用私钥签出激活码 → 程序用内置公钥验签**。

两种码：

- **绑机码**：正文里带序列号，只在那台机器上生效，转发给别人无效；
- **免绑定码**：正文里 ``bind="none"``，不看序列号，拿到即可用。发给特别用户，
  建议一律带有效期——离线方案没有吊销手段，有效期是唯一的把手。

模块边界：
- :mod:`hardware` 只管「本机是谁」，派生序列号，不碰授权语义；
- :mod:`code` 只管「这张码是不是真的、写了什么」，不碰硬件；
- :mod:`store` 只管激活码存哪儿。

三者都不做混淆或反调试：客户端校验必然可被绕过，这套东西挡的是随手转发，不挡逆向。
"""

from __future__ import annotations

from datetime import date

from .code import Entitlement, License, LicenseError, evaluate, verify_code
from .hardware import current_serial, serial_is_wellformed
from .levels import Level, level_label, load_levels
from .store import clear_code, license_path, load_code, save_code

#: 授权按**等级**划分，等级表见 ``levels.json``（``load_levels()`` 读取）。
#: 激活码正文里写的是 ``lv1`` 这样的等级名，门禁处问的也是等级：功能会增删改名，
#: 等级不会——将来某个功能划进 Lv1，已签发的码自动覆盖它，不用给老用户重发。
#:
#: **目前没有任何功能接入门禁**（后台截图曾短暂挂过，现已直接开放）。整套链路是
#: 通的，只是暂时没有消费者。要把某个功能设为高级功能，在它的 UI 入口与能力入口
#: 各校验一次 ``has_feature("lv1")``。

__all__ = [
    "Entitlement",
    "Level",
    "License",
    "LicenseError",
    "clear_code",
    "current_entitlement",
    "current_serial",
    "has_feature",
    "level_label",
    "license_path",
    "load_levels",
    "load_code",
    "refresh_entitlement",
    "save_code",
    "serial_is_wellformed",
    "try_activate",
    "verify_code",
]

#: 进程级缓存：验签和读硬件都不是每次调用都要重做的事
_cached: Entitlement | None = None


def current_entitlement() -> Entitlement:
    """当前授权结论，进程内缓存。"""
    global _cached
    if _cached is None:
        _cached = evaluate(load_code(), current_serial())
        issued = _cached.license
        if issued is not None:
            feats = "、".join(sorted(_cached.features)) or "无"
            from loguru import logger
            logger.info(f"高级功能已激活（{issued.code_id}）：{feats}")
    return _cached


def refresh_entitlement() -> Entitlement:
    """丢弃缓存重新判定（填写或清除激活码后调用）"""
    global _cached
    _cached = None
    return current_entitlement()


def has_feature(feature: str) -> bool:
    """该高级功能当前是否可用。未激活、过期、不匹配一律返回 ``False``。"""
    return current_entitlement().has(feature)


def try_activate(code: str, today: date | None = None) -> Entitlement:
    """校验并保存一张激活码；校验不通过不写盘，返回结论供 UI 提示原因。"""
    result = evaluate(code, current_serial(), today)
    if result.active:
        save_code(code)
        global _cached
        _cached = result
    return result
