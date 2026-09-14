"""import 路径沙盒：只接受相对 workflows 根、以文件名开头的路径。

`.wf` 会被引擎执行，所以 import 能指向哪里就是「能执行什么」。历史实现里
绝对路径完全绕过校验、`..` 只被用来跳过跨层解析却没有拦截，两种写法都能
把 workflows 目录之外的文件加载进来（实测均成功）。
"""

from __future__ import annotations

import pytest

from lvjiang.workflows.engine.core import _normalize_import_path
from lvjiang.workflows.errors import WorkflowUserError
from tests.case_matrix import case_matrix


class TestAcceptsRootRelative:
    @case_matrix(
        ("raw", "expected"),
        [
            ("subcall/navigation.wf", "subcall/navigation.wf"),
            ("a.wf", "a.wf"),
            ("subcall/./x.wf", "subcall/x.wf"),      # 冗余写法规整掉
            ("subcall//x.wf", "subcall/x.wf"),
            ("  subcall/x.wf  ", "subcall/x.wf"),    # 两端空白
        ],
    )
    def test_normalizes(self, raw, expected):
        assert _normalize_import_path(raw) == expected


class TestRejectsEscapes:
    @case_matrix(
        "raw",
        [
            "/etc/passwd.wf",           # POSIX 绝对
            "~/evil.wf",                # 家目录
            "./x.wf",                   # 相对当前目录
            "../x.wf",                  # 直接逃逸
            "subcall/../../../x.wf",    # 以文件名开头，但中段逃逸
            "",
            "   ",
        ],
    )
    def test_rejects(self, raw):
        with pytest.raises(WorkflowUserError):
            _normalize_import_path(raw)

    @case_matrix(
        "raw",
        [
            "C:/evil.wf",
            "C:\\evil.wf",
            "\\\\server\\share\\x.wf",   # UNC
            "\\evil.wf",
            "subcall\\x.wf",             # 反斜杠分隔符
        ],
    )
    def test_rejects_windows_forms_even_on_posix(self, raw):
        """Windows 形态必须在任何平台都被拒。

        不能依赖 Path.is_absolute()——它随平台变：C:/evil.wf 与 UNC 路径在
        Linux 上判定为「非绝对」，而开发和 CI 都在 Linux、用户却在 Windows，
        这类写法会静默漏过。
        """
        with pytest.raises(WorkflowUserError):
            _normalize_import_path(raw)


def test_remote_workflows_are_explicitly_append_only():
    """远程 WF 是有意开放的可执行内容，只能新增且必须保持来源警告。

    路径沙盒仍限制 import 范围；内容信任则由 append_only、不覆盖 system、
    SHA 不可变和界面强制 `[远程]` 警告共同表达，不能退化成普通版本覆盖。
    """
    from lvjiang.core.config import versioning

    for path in ("workflows/daily.wf", "workflows/subcall/nav.wf",
                 "workflows/batch/prepare_item.wf"):
        spec = versioning.spec_for(path)
        assert spec is not None
        assert spec.remote_mode == "append_only"
        assert spec.allow_remote_new is True
