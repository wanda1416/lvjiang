"""import 路径沙盒：只接受相对 workflows 根、以文件名开头的路径。

`.wf` 会被引擎执行，所以 import 能指向哪里就是「能执行什么」。历史实现里
绝对路径完全绕过校验、`..` 只被用来跳过跨层解析却没有拦截，两种写法都能
把 workflows 目录之外的文件加载进来（实测均成功）。
"""

from __future__ import annotations

import pytest

from lvjiang.workflows.engine.core import _normalize_import_path
from lvjiang.workflows.errors import WorkflowUserError


class TestAcceptsRootRelative:
    @pytest.mark.parametrize(
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
    @pytest.mark.parametrize(
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

    @pytest.mark.parametrize(
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


def test_workflows_must_not_be_remotely_deliverable():
    """workflows 不得进在线下发注册表——那等于远程代码执行。

    scenes / layouts / tuning_rules 是数据，下发只改识别与判定；`.wf` 会被
    引擎执行，下发它意味着远程可以让本机跑任意脚本。现在它「碰巧没注册」，
    这条把它变成显式约束。
    """
    from lvjiang.core.config import versioning

    for path in ("workflows/daily.wf", "workflows/subcall/nav.wf",
                 "workflows/batch/prepare_item.wf"):
        assert versioning.spec_for(path) is None, (
            f"{path} 进入了下发注册表——.wf 可执行，下发即 RCE")
