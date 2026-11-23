"""Windows native crash 异常详情解析测试。"""
import ctypes
import re
import sys
from ctypes import wintypes

import pytest

from lvjiang.core.crash_handler import _format_windows_exception


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only SEH layout")
def test_format_windows_exception_parses_code_access_and_module():
    class _REC(ctypes.Structure):
        _fields_ = [
            ("ExceptionCode", wintypes.DWORD),
            ("ExceptionFlags", wintypes.DWORD),
            ("ExceptionRecord", ctypes.c_void_p),
            ("ExceptionAddress", ctypes.c_void_p),
            ("NumberParameters", wintypes.DWORD),
            ("ExceptionInformation", ctypes.c_size_t * 15),
        ]

    class _PTRS(ctypes.Structure):
        _fields_ = [
            ("ExceptionRecord", ctypes.POINTER(_REC)),
            ("ContextRecord", ctypes.c_void_p),
        ]

    kernel32 = ctypes.windll.kernel32
    fn_addr = ctypes.cast(kernel32.GetCurrentThreadId, ctypes.c_void_p).value
    rec = _REC()
    rec.ExceptionCode = 0xC0000005
    rec.ExceptionAddress = fn_addr
    rec.NumberParameters = 2
    rec.ExceptionInformation[0] = 0  # Read
    rec.ExceptionInformation[1] = 0x18
    ptrs = _PTRS(ExceptionRecord=ctypes.pointer(rec), ContextRecord=None)

    text = "\n".join(_format_windows_exception(ctypes.addressof(ptrs)))
    assert "0xC0000005 (ACCESS_VIOLATION)" in text
    assert "Access: Read at 0x18" in text
    assert "kernel32.dll+0x" in text.lower()
    assert re.search(r"^ThreadId: 0x[0-9a-f]{8}$", text, re.MULTILINE)
