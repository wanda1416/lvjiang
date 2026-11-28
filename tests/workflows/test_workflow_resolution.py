"""工作流引用解析：按 id / 文件名 / 相对路径落到同一份文件。

回归点：任务以 ``#% id`` 被引用，目录只是组织方式。解析器是"搬目录不用
改调用点"的唯一支点——它一旦只认精确路径，每次调整结构都会在 Python 与
测试里留下一串字符串路径，漏一处就是运行期 ``FileNotFoundError``。

同名歧义必须报错：``common.wf`` 这种名字在不同目录各有一份时，静默选一个
会让"到底跑了哪份脚本"取决于目录遍历顺序。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvjiang.workflows import discovery
from lvjiang.workflows.errors import WorkflowUserError


class _StubResolver:
    """只实现解析器协议里用到的两个方法，映射 ``rel -> 绝对路径``。"""

    def __init__(self, files: dict[str, Path]):
        self._files = files

    def resolve_read(self, rel: str) -> str | None:
        prefix = f"{discovery.WORKFLOWS_DIR}/"
        if not rel.startswith(prefix):
            return None
        found = self._files.get(rel[len(prefix):])
        return str(found) if found is not None else None

    def enumerate_entity_tree(self, root: str, pattern: str) -> list[str]:
        assert root == discovery.WORKFLOWS_DIR
        assert pattern == "*.wf"
        return sorted(self._files)


@pytest.fixture
def wired(monkeypatch, tmp_path: Path):
    """构造一份虚拟 workflows 树，并把发现层与解析器都指向它。"""

    def make(files: dict[str, str], scripts: list[dict] | None = None):
        mapping = {}
        for rel in files:
            path = tmp_path / rel.replace("/", "_")
            path.write_text("return 0\n", encoding="utf-8")
            mapping[rel] = path
        monkeypatch.setattr(discovery, "get_root_resolver", lambda: _StubResolver(mapping))
        monkeypatch.setattr(discovery, "discover_scripts", lambda: list(scripts or []))
        return mapping

    return make


def test_resolves_by_script_id_after_directory_move(wired):
    """搬目录只改 wf_file，id 不变；按 id 仍能落到新位置。"""
    files = wired(
        {"scan/daily_jianghu.wf": "x"},
        scripts=[{"id": "daily_jianghu", "wf_file": "scan/daily_jianghu.wf"}],
    )
    assert discovery.resolve_workflow_reference("daily_jianghu") == files["scan/daily_jianghu.wf"]


def test_resolves_by_bare_filename_and_relative_path(wired):
    files = wired(
        {
            "daily/daily_jianghu.wf": "x",
            "subcall/navigation.wf": "y",
        },
        scripts=[{"id": "daily_jianghu", "wf_file": "daily/daily_jianghu.wf"}],
    )
    by_name = discovery.resolve_workflow_reference("daily_jianghu.wf")
    by_rel = discovery.resolve_workflow_reference("subcall/navigation.wf")
    assert by_name == files["daily/daily_jianghu.wf"]
    assert by_rel == files["subcall/navigation.wf"]


def test_stale_path_falls_back_to_id(wired):
    """任务里存的是 wf_file 快照；升级后旧路径失效，仍要按 id 找到。"""
    files = wired(
        {"scan/scan_wallet.wf": "x"},
        scripts=[{"id": "scan_wallet", "wf_file": "scan/scan_wallet.wf"}],
    )
    assert discovery.resolve_workflow_reference(
        "scan_wallet.wf") == files["scan/scan_wallet.wf"]


def test_non_runnable_library_resolves_by_filename(wired):
    """过程库没有 id，只能按文件名兜底——批量生命周期 wf 同理。"""
    files = wired({"subcall/baiye/common.wf": "x"})
    assert discovery.resolve_workflow_reference("common") == files["subcall/baiye/common.wf"]


def test_missing_absolute_path_does_not_fall_back(wired, tmp_path: Path):
    """显式给绝对路径时，找不到就是找不到，不能悄悄换成另一份文件。"""
    wired({"scan/scan_wallet.wf": "x"},
          scripts=[{"id": "scan_wallet", "wf_file": "scan/scan_wallet.wf"}])
    assert discovery.resolve_workflow_reference(tmp_path / "nope" / "scan_wallet.wf") is None


def test_unknown_reference_returns_none(wired):
    wired({"daily/daily_jianghu.wf": "x"})
    assert discovery.resolve_workflow_reference("no_such_workflow") is None
    assert discovery.resolve_workflow_reference("") is None


def test_duplicate_stem_is_an_error_not_a_coin_flip(wired):
    wired({"a/common.wf": "x", "b/common.wf": "y"})
    with pytest.raises(WorkflowUserError, match="多个候选"):
        discovery.resolve_workflow_reference("common")
