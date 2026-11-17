"""配置文本一律 LF：Windows 上任何保存都不能把行尾翻成 CRLF。

CRLF 不会在 git 状态里显形（``.gitattributes`` 的 ``eol=lf`` 会归一化），
但它会改变预置包/离线包的字节摘要、让同一份配置在不同机器上出现「改一行
全改」的假 diff，也让编辑器与工具链行为不一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from lvjiang.core.config import save_yaml
from lvjiang.core.fs_util import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_save_yaml_writes_lf_only(tmp_path):
    target = tmp_path / "sample.yaml"
    save_yaml(target, {"name": {"zh_CN": "手游"}, "items": [1, 2]})
    data = target.read_bytes()
    assert b"\r\n" not in data
    assert data.count(b"\n") >= 3
    # 内容仍然可解析、且中文不转义
    assert yaml.safe_load(data.decode("utf-8"))["name"]["zh_CN"] == "手游"


def test_atomic_write_text_writes_lf_only(tmp_path):
    target = tmp_path / "sample.json"
    atomic_write_text(target, '{\n  "a": 1\n}\n', prefix=".t-")
    assert b"\r\n" not in target.read_bytes()


def _call_spans(source: str, needle: str) -> list[tuple[int, str]]:
    """括号配平取出每次调用的完整实参文本，附起始行号。"""
    spans: list[tuple[int, str]] = []
    for match in re.finditer(re.escape(needle), source):
        start = match.end()
        depth, i = 1, start
        while i < len(source) and depth:
            char = source[i]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            i += 1
        spans.append((source.count("\n", 0, match.start()) + 1,
                      source[start:i - 1]))
    return spans


def _text_write_calls_missing_newline(source: str) -> list[int]:
    """找写文本却没声明 ``newline`` 的调用行号。

    只认三种文本写形态：``.write_text(...)``、``.open("w", ...)``（``Path.open``，
    模式是第一参数）、以及带 ``encoding=`` 的 ``open(path, "w", ...)`` /
    ``os.fdopen(fd, "w", ...)``。``ZipFile.open(name, "w")``、``av.open(...)``
    这类同名字但写二进制/其它语义的调用不在此列。
    """
    offenders: list[int] = []
    for line_no, args in _call_spans(source, ".write_text("):
        if "newline" not in args:
            offenders.append(line_no)
    for line_no, args in _call_spans(source, ".open("):
        stripped = args.lstrip()
        if not stripped.startswith(('"w"', "'w'")):
            continue
        if "newline" not in args:
            offenders.append(line_no)
    for line_no, args in _call_spans(source, "open("):
        if "encoding=" not in args or not re.search(r"[\"']w[\"']", args):
            continue
        if "newline" not in args:
            offenders.append(line_no)
    return offenders


def test_src_text_writers_declare_newline():
    """守卫：新加的文本写入必须显式 newline="\\n"，否则 Windows 上写回 CRLF。"""
    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for line_no in _text_write_calls_missing_newline(
                path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no}")
    assert offenders == [], "文本写入缺 newline='\\n':\n" + "\n".join(offenders)
