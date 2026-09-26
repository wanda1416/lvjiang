"""平台权限能力测试。"""
from __future__ import annotations

import ctypes
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
