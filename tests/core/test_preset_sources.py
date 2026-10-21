"""预置输入清单：以 git 索引为界，文本行尾统一 LF。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from lvjiang.core.preset_sources import (
    TEXT_SUFFIXES,
    excluded_files,
    format_excluded,
    normalize_text,
    normalize_tree,
    present_files,
    tracked_files,
)

GIT = shutil.which("git")
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(GIT is None, reason="需要 git 命令")
def test_tracked_files_lists_only_index_entries(tmp_path):
    """工作区里的未跟踪草稿不能进清单；中文文件名也不能被 git 引号转义。"""
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True, capture_output=True)
    workflows = tmp_path / "config/system/workflows"
    workflows.mkdir(parents=True)
    (workflows / "task.wf").write_text("wait 0.01\n", encoding="utf-8")
    (workflows / "_editor_run.wf").write_text("draft\n", encoding="utf-8")
    subprocess.run([GIT, "-C", str(tmp_path), "add", "config/system/workflows/task.wf"],
                   check=True, capture_output=True)

    assert tracked_files(tmp_path, "config/system") == ["workflows/task.wf"]


@pytest.mark.parametrize("outcome", ["missing-git", "not-a-repo"])
def test_tracked_files_returns_none_when_git_unavailable(monkeypatch, tmp_path, outcome):
    """没有 git 命令、或不在 git 仓库内时返回 None，由调用方决定整树回退。"""
    def fake_run(*_args, **_kwargs):
        if outcome == "missing-git":
            raise OSError("git 不存在")
        return subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"")

    monkeypatch.setattr("lvjiang.core.preset_sources.subprocess.run", fake_run)
    assert tracked_files(tmp_path, "config/system") is None


def test_present_files_lists_relative_posix_paths(tmp_path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows/task.wf").write_text("x", encoding="utf-8")
    assert present_files(tmp_path) == ["workflows/task.wf"]
    assert present_files(tmp_path / "missing") == []


def test_excluded_files_is_present_minus_index():
    assert excluded_files(["a.wf", "b.wf", "c.yaml"], ["a.wf", "c.yaml"]) == ["b.wf"]
    assert excluded_files(["a.wf"], ["a.wf"]) == []


def test_format_excluded_previews_long_lists():
    excluded = [f"workflows/draft{i}.wf" for i in range(12)]
    message = format_excluded("config/system", excluded)
    assert "共 12 个" in message
    assert message.count("、") == 9          # 预览 10 项 = 9 个分隔符
    assert format_excluded("config/system", []) == ""


def test_normalize_text_lowers_crlf_only_for_text_files():
    assert normalize_text("a.wf", b"line\r\nnext\r\n") == b"line\nnext\n"
    assert normalize_text("a.YAML", b"k: v\r\n") == b"k: v\n"
    # PNG 签名 \x89PNG\r\n\x1a\n 自带 CRLF：二进制必须原样保留，不能被替换毁掉
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(16))
    assert normalize_text("a.png", png) == png
    assert normalize_tree({"a.png": png, "b.yaml": b"k: v\r\n"}) == {
        "a.png": png, "b.yaml": b"k: v\n"}


def test_text_suffixes_cover_gitattributes_lf_rules():
    """漏一个 eol=lf 后缀，跨机器摘要就会重新漂移。"""
    attributes = REPO_ROOT / ".gitattributes"
    if not attributes.is_file():
        pytest.skip("不在仓库工作区")
    declared = set()
    for line in attributes.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 3 and parts[1] == "text" and "eol=lf" in parts[2:]:
            # 只看按后缀声明的规则；路径限定规则（如 config/system/**/*.md）
            # 不改变「哪些后缀算文本」这个契约。
            if parts[0].startswith("*.") and "/" not in parts[0]:
                declared.add(parts[0][1:].lower())
    assert declared, "没解析到任何 eol=lf 规则"
    assert declared <= TEXT_SUFFIXES, f"未覆盖的后缀: {sorted(declared - TEXT_SUFFIXES)}"
