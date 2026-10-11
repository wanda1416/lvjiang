"""应用版本的 PEP 440 子集；不依赖配置、Qt 或业务插件。"""
from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

_NUMBER = r"(?:0|[1-9]\d*)"
_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:\.{_NUMBER}|(?:b|rc)[1-9]\d*)?")


def application_version(value: str) -> Version:
    """三段正式版、既有四段修订版，以及三段 bN/rcN；不接受隐式规范化。"""
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise InvalidVersion(f"无效应用版本 {value!r}，应为 X.Y.Z、X.Y.Z.N、X.Y.ZbN 或 X.Y.ZrcN")
    return Version(value)


def application_series(value: str) -> str:
    return ".".join(map(str, application_version(value).release[:3]))


def update_namespace(value: str) -> str:
    """预发布基底各自隔离；已有正式四段修订仍共享同系列基底。"""
    version = application_version(value)
    return str(version) if version.is_prerelease else application_series(value)


def config_version(value: str) -> Version:
    """配置只比较三段系列，软件升级另按完整版本比较。"""
    return Version(application_series(value))
