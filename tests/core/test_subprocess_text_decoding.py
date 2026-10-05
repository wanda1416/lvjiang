"""读 CLI 输出必须显式指定编码，不能跟 locale 走。

`text=True` 不带 encoding 时按**本地 locale** 解码：中文 Windows 上是 GBK，而
adb / Android 的输出是 UTF-8。后果有两级：

- 轻：中文窗口标题、应用名解成乱码，识别和日志全错，但不报错，没人发现；
- 重：直接抛 UnicodeDecodeError。`dumpsys window windows` 有几百 KB，里面混进
  一个非 GBK 字节，subprocess 的读取线程就在 `fh.read()` 处炸掉——调用方只拿到
  一条孤立的线程 traceback（`Exception in thread Thread-N (_readerthread)`），
  而那次前台应用探测静默失败，自动化继续跑在错误的假设上。

这里只留不需要真实 adb 的两类断言：编码常量本身，以及"新增 subprocess 漏写
编码"的源码门禁。真实通道行为由运行环境决定，造一个假 adb 去测没有意义。
"""
from __future__ import annotations

from pathlib import Path

from lvjiang.core.platforms import SUBPROCESS_TEXT

_SRC = Path(__file__).resolve().parents[2] / "src" / "lvjiang"
#: 0xAA 单独出现时不是合法的 GBK 序列，也不是合法的 UTF-8 起始字节
_BAD_BYTE = 0xAA


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
