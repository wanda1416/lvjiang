"""跨平台版本与网络错误文案，不依赖桌面 UI。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from ..i18n import tr


def network_access_error_message(
        url: str, resource: Literal["update", "announcement"]) -> str:
    """面向普通用户的统一网络访问失败说明。"""
    if resource == "update":
        return tr(
            "可能因为网络原因，无法访问 {url} 获取更新，请检查网络设置后重试"
        ).format(url=url)
    return tr(
        "可能因为网络原因，无法访问 {url} 获取公告，请检查网络设置后重试"
    ).format(url=url)


def get_version() -> str:
    """获取版本号

    优先级：
    1. _version.py（打包时注入）
    2. importlib.metadata（已安装包）
    3. pyproject.toml（开发环境）
    """
    # 1. 优先从 _version.py 读取（打包时注入）
    try:
        from .._version import __version__
        if __version__ and __version__ != "0.0.0.dev0":
            return __version__
    except Exception:
        pass

    # 2. 从 package metadata 读取（已安装时）
    try:
        from importlib.metadata import version
        return version("lvjiang")
    except Exception:
        pass

    # 3. 从 pyproject.toml 读取（开发环境）
    try:
        import tomllib
        # __file__ = src/lvjiang/core/version_info.py
        # 需要向上 4 级到项目根目录
        pyproject = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("project", {}).get("version", "unknown")
    except Exception:
        pass

    return "unknown"


def parse_version(version: str) -> list[int]:
    """解析版本号为可比较的整数列表

    自动去除预发布后缀（如 0.5.0b0 → [0, 5, 0]）
    """
    try:
        # 去掉预发布后缀：a/b/rc/alpha/beta/dev 及其数字
        cleaned = re.sub(r'[.-]?(a|alpha|b|beta|rc|dev)\d*$', '', version, flags=re.IGNORECASE)
        return [int(x) for x in cleaned.split(".")]
    except (ValueError, AttributeError):
        return [0]
