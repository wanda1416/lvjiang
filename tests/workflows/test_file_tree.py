"""工作流文件树的合并语义。

编辑器展示的是「有哪些脚本」，不是「哪层有哪些」。所以同一路径只出现一个
节点，显示实际生效的那一份；出厂文件只读，用户要改得先复制到 local。
"""

from __future__ import annotations

import pytest

from lvjiang.core.config import resolver as resolver_mod
from lvjiang.workflows.file_tree import (
    WorkflowFile,
    list_directories,
    list_workflow_files,
)

_WF = 'log "x"\n'


@pytest.fixture
def layers(tmp_path, monkeypatch):
    """搭一对空的 system/local 层，返回两个 workflows 根。"""
    system = tmp_path / "system" / "workflows"
    local = tmp_path / "local" / "workflows"
    system.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.setattr(
        resolver_mod, "_resolver",
        resolver_mod.ConfigResolver(
            system_dir=tmp_path / "system", local_dir=tmp_path / "local",
            dev_mode=False),
    )
    return system, local


def _write(root, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_WF, encoding="utf-8")


def _by_path(files: list[WorkflowFile]) -> dict[str, WorkflowFile]:
    return {f.rel_path: f for f in files}


class TestMergedView:
    def test_union_of_both_layers(self, layers):
        system, local = layers
        _write(system, "a.wf")
        _write(local, "b.wf")

        assert [f.rel_path for f in list_workflow_files()] == ["a.wf", "b.wf"]

    def test_same_name_appears_once_as_local(self, layers):
        """local 影子整份顶掉 system —— 树上只能有一个节点。"""
        system, local = layers
        _write(system, "dup.wf")
        _write(local, "dup.wf")

        files = list_workflow_files()

        assert [f.rel_path for f in files] == ["dup.wf"]
        assert files[0].layer == "local"
        assert files[0].overrides_system is True

    def test_local_only_file_is_not_marked_as_override(self, layers):
        _, local = layers
        _write(local, "mine.wf")

        f = _by_path(list_workflow_files())["mine.wf"]

        assert f.layer == "local"
        assert f.overrides_system is False

    def test_recurses_into_subdirectories(self, layers):
        system, local = layers
        _write(system, "subcall/nav.wf")
        _write(local, "batch/mine.wf")

        files = list_workflow_files()

        assert [f.rel_path for f in files] == [
            "batch/mine.wf", "subcall/nav.wf"]
        assert list_directories(files) == ["batch", "subcall"]

    def test_local_can_add_a_directory_system_lacks(self, layers):
        _, local = layers
        _write(local, "mine/deep/x.wf")

        files = list_workflow_files()

        assert [f.rel_path for f in files] == ["mine/deep/x.wf"]
        assert list_directories(files) == ["mine", "mine/deep"]

    def test_shows_everything_including_underscore_files(self, layers):
        """树不做任何过滤：磁盘上有什么就显示什么。

        发现层会跳过 ``_`` 前缀（它们不该注册成可启动脚本），但那是发现层
        的事。树藏起来只会让人找不到自己刚录的 _recorded.wf。
        """
        system, local = layers
        _write(system, "_editor_run.wf")
        _write(local, "_recorded.wf")
        _write(system, "archived/old.wf")
        _write(system, "real.wf")

        assert [f.rel_path for f in list_workflow_files()] == [
            "_editor_run.wf", "_recorded.wf", "archived/old.wf", "real.wf"]

    def test_shows_underscore_directories_too(self, layers):
        system, _ = layers
        _write(system, "_scratch/x.wf")

        files = list_workflow_files()

        assert [f.rel_path for f in files] == ["_scratch/x.wf"]
        assert list_directories(files) == ["_scratch"]


class TestEditability:
    def test_system_file_is_read_only(self, layers):
        system, _ = layers
        _write(system, "factory.wf")

        f = _by_path(list_workflow_files())["factory.wf"]

        assert f.is_system
        assert f.editable is False, "出厂文件必须只读，改之前要先复制到本地"

    def test_local_file_is_editable(self, layers):
        _, local = layers
        _write(local, "mine.wf")

        assert _by_path(list_workflow_files())["mine.wf"].editable is True

    def test_copying_to_local_makes_it_editable(self, layers):
        """「复制到本地」之后同一路径变为可编辑，且标记为覆盖出厂。"""
        system, local = layers
        _write(system, "factory.wf")
        assert _by_path(list_workflow_files())["factory.wf"].editable is False

        _write(local, "factory.wf")          # 模拟复制到本地

        f = _by_path(list_workflow_files())["factory.wf"]
        assert f.editable is True
        assert f.overrides_system is True


class TestPathHelpers:
    @pytest.mark.parametrize(
        ("rel", "name", "parent"),
        [
            ("a.wf", "a.wf", ""),
            ("subcall/nav.wf", "nav.wf", "subcall"),
            ("a/b/c.wf", "c.wf", "a/b"),
        ],
    )
    def test_name_and_parent(self, rel, name, parent):
        f = WorkflowFile(rel_path=rel, layer="system", overrides_system=False)
        assert (f.name, f.parent) == (name, parent)
