"""读 CLI 输出必须显式指定编码，不能跟 locale 走。

`text=True` 不带 encoding 时按**本地 locale** 解码：中文 Windows 上是 GBK，而
adb / Android 的输出是 UTF-8。后果有两级：

- 轻：中文窗口标题、应用名解成乱码，识别和日志全错，但不报错，没人发现；
- 重：直接抛 UnicodeDecodeError。`dumpsys window windows` 有几百 KB，里面混进
  一个非 GBK 字节，subprocess 的读取线程就在 `fh.read()` 处炸掉——调用方只拿到
  一条孤立的线程 traceback（`Exception in thread Thread-N (_readerthread)`），
  而那次前台应用探测静默失败，自动化继续跑在错误的假设上。

这组用例在 Linux 上也有意义：它们把编码写死在断言里，不依赖运行机器的 locale。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from lvjiang.core.android.device import AdbDevice
from lvjiang.core.platforms import SUBPROCESS_TEXT

_SRC = Path(__file__).resolve().parents[2] / "src" / "lvjiang"
#: 0xAA 单独出现时不是合法的 GBK 序列，也不是合法的 UTF-8 起始字节
_BAD_BYTE = 0xAA


def _fake_adb(tmp_path: Path, payload: str) -> Path:
    """造一个假 adb：原样吐出一段混了非法字节的字节流。"""
    script = tmp_path / "fake_adb.py"
    script.write_text(payload, encoding="utf-8")
    launcher = tmp_path / ("fake_adb.cmd" if sys.platform == "win32"
                           else "fake_adb.sh")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n',
                            encoding="utf-8")
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                            encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def test_adb_shell_survives_bytes_the_locale_cannot_decode(tmp_path):
    """坏字节换成 U+FFFD，中文照常解出，调用方要的字段仍可解析。

    用 replace 而不是 ignore：ignore 会悄悄删字符，replace 留下的 U+FFFD 至少
    能让人看出这里原本有东西。
    """
    payload = (
        "import sys\n"
        "out = sys.stdout.buffer\n"
        "out.write('窗口标题'.encode('utf-8'))\n"
        f"out.write(bytes([{_BAD_BYTE}]))\n"
        "out.write(b'  mResumedActivity com.example/.MainActivity\\n')\n"
    )
    device = AdbDevice(adb_path=str(_fake_adb(tmp_path, payload)))

    output = device.shell("dumpsys", "activity", "activities")

    assert "窗口标题" in output, "中文必须按 UTF-8 正确解出"
    assert "�" in output, "非法字节应保留成 U+FFFD，而不是被丢弃"
    assert re.search(r"mResumedActivity\s+com\.example/\.MainActivity", output)


def test_the_same_bytes_blow_up_under_a_locale_codec():
    """反向确认这组字节真的会在 GBK 下炸——否则上面那条用例什么都没证明。"""
    payload = (
        "import sys\n"
        "sys.stdout.buffer.write('窗口标题'.encode('utf-8'))\n"
        f"sys.stdout.buffer.write(bytes([{_BAD_BYTE}]))\n"
    )
    script = Path(__file__).parent / "_tmp_locale_probe.py"
    script.write_text(payload, encoding="utf-8")
    try:
        with pytest.raises(UnicodeDecodeError):
            subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, encoding="gbk")
    finally:
        script.unlink(missing_ok=True)


def test_shared_constant_pins_utf8_and_replace():
    assert SUBPROCESS_TEXT == {
        "text": True, "encoding": "utf-8", "errors": "replace"}


def test_no_source_file_reads_cli_output_with_a_bare_text_flag():
    """门禁：新增 subprocess 时漏掉编码，正是这个 bug 的来源。

    `text=True` 必须经 SUBPROCESS_TEXT / SUBPROCESS_TEXT_OEM 走，不能裸写——
    否则解码方式取决于运行这台机器的 locale，Linux 开发机上永远测不出来。
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "is_text=True" in stripped:
                continue
            if "text=True" in stripped and "SUBPROCESS_TEXT" not in stripped:
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {stripped}")
    assert offenders == [], "\n".join(offenders)
