"""平台权限能力测试。"""
from __future__ import annotations

import ctypes
import os
import sys
from types import SimpleNamespace

from lvjiang.core import platforms


class _Function:
    def __init__(self, impl):
        self._impl = impl
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._impl(*args)


def test_process_elevation_reads_windows_token(monkeypatch):
    closed = []

    def open_token(_process, _access, token_pointer):
        token_pointer._obj.value = 123  # noqa: SLF001 - ctypes output pointer
        return 1

    def get_token_info(_token, _kind, elevation_pointer, _size, returned_pointer):
        elevation_pointer._obj.TokenIsElevated = 1  # noqa: SLF001
        returned_pointer._obj.value = 4  # noqa: SLF001
        return 1

    kernel32 = SimpleNamespace(
        GetCurrentProcess=_Function(lambda: -1),
        CloseHandle=_Function(lambda handle: closed.append(handle.value) or 1),
    )
    advapi32 = SimpleNamespace(
        OpenProcessToken=_Function(open_token),
        GetTokenInformation=_Function(get_token_info),
    )
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(
        ctypes, "windll",
        SimpleNamespace(kernel32=kernel32, advapi32=advapi32),
        raising=False,
    )

    assert platforms.is_process_elevated() is True
    assert closed == [123]


def test_process_elevation_is_not_a_non_windows_concept(monkeypatch):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)

    assert platforms.is_process_elevated() is None


def test_clean_subprocess_env_restores_and_strips_pyinstaller_ld_library_path(monkeypatch):
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)

    # 1. 存在非空 LD_LIBRARY_PATH_ORIG 时恢复原值并移除 PyInstaller 变量
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cleaned = platforms.clean_subprocess_env({
        "PATH": "/usr/bin",
        "_MEIPASS2": "/opt/lvjiang/_internal",
        "LD_LIBRARY_PATH": "/opt/lvjiang/_internal:/custom/lib",
        "LD_LIBRARY_PATH_ORIG": "/custom/lib",
    })
    assert "_MEIPASS2" not in cleaned
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert cleaned["LD_LIBRARY_PATH"] == "/custom/lib"

    # 2. LD_LIBRARY_PATH_ORIG 为空串时直接移除 LD_LIBRARY_PATH
    cleaned_empty_orig = platforms.clean_subprocess_env({
        "LD_LIBRARY_PATH": "/opt/lvjiang/_internal",
        "LD_LIBRARY_PATH_ORIG": "",
    })
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned_empty_orig
    assert "LD_LIBRARY_PATH" not in cleaned_empty_orig

    # 3. 无 _ORIG 的 frozen 态按 _MEIPASS 精准剔除（仅剩它时移除键，有其他路径时保留）
    monkeypatch.setattr(sys, "_MEIPASS", "/opt/lvjiang/_internal", raising=False)
    sole = platforms.clean_subprocess_env({
        "LD_LIBRARY_PATH": "/opt/lvjiang/_internal/",
    })
    assert "LD_LIBRARY_PATH" not in sole

    paired = platforms.clean_subprocess_env({
        "LD_LIBRARY_PATH": "/opt/lvjiang/_internal:/usr/local/lib",
    })
    assert paired["LD_LIBRARY_PATH"] == "/usr/local/lib"

    # 4. 非 frozen 源码态且无 PyInstaller 变量时保留原有 LD_LIBRARY_PATH
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    source_env = platforms.clean_subprocess_env({
        "LD_LIBRARY_PATH": "/usr/local/lib",
    })
    assert source_env["LD_LIBRARY_PATH"] == "/usr/local/lib"


def test_subprocess_clean_env_kwargs_platform_and_frozen_gates(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/lvjiang/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.delenv("_MEIPASS2", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/opt/lvjiang/_internal", raising=False)

    # Windows 上保持空字典，零行为变化
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    assert platforms.subprocess_clean_env_kwargs() == {}

    # 非 Windows frozen 返回清洗后的 env
    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    kwargs = platforms.subprocess_clean_env_kwargs()
    assert "env" in kwargs
    assert "LD_LIBRARY_PATH" not in kwargs["env"]

    # 非 Windows 非 frozen 且无 PyInstaller 环境变量时返回空字典
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert os.environ.get("LD_LIBRARY_PATH") == "/opt/lvjiang/_internal"
    assert platforms.subprocess_clean_env_kwargs() == {}
