"""场景编辑器版本控件的来源与版本数据。

远程下发的配置会顶替系统文件。不标出来的话，开发者本地跑出来的行为和用户
不一样却毫不知情，用户报"识别坏了"时根本复现不出——而这恰恰是在线下发最
需要被排查的一类问题。

主要测试 `describe_entity_version` 纯函数而不是 SceneTab 控件：构造 SceneTab
要拉起整个控件树，在测试里反复创建/析构会触发 item delegate 的析构时序
问题（PyQt 已知坑，与本功能无关，HEAD 上同样复现）。
"""
from __future__ import annotations

import pytest

import lvjiang.core.config.resolver as resolver_mod
from lvjiang.core.config import versioning
from lvjiang.ui.scene_editor.scene_tab import SceneTab, describe_entity_version

_SCENE = "activity_main"
_LAYOUT = "默认布局"


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """把三层根目录指到 tmp_path，返回写各层文件的助手。"""
    system, local, remote = (tmp_path / n for n in ("system", "local", "remote"))
    for d in (system, local, remote):
        (d / "scenes").mkdir(parents=True)
        (d / "layouts" / _LAYOUT).mkdir(parents=True)

    def write_scene(root, version):
        (root / "scenes" / f"{_SCENE}.yaml").write_text(
            f"content_version: {version}\nkey: {_SCENE}\nname: x\n",
            encoding="utf-8")

    def write_layout(root, version):
        (root / "layouts" / _LAYOUT / f"{_SCENE}.json").write_text(
            f'{{"content_version": {version}, "regions": []}}', encoding="utf-8")

    def install(dev_mode=False):
        monkeypatch.setattr(
            resolver_mod, "_resolver",
            resolver_mod.ConfigResolver(system_dir=system, local_dir=local,
                                        remote_dir=remote, dev_mode=dev_mode))

    return system, local, remote, write_scene, write_layout, install


class TestOriginText:
    def test_shows_system_source_and_version(self, layered):
        system, _, _, write_scene, _, install = layered
        write_scene(system, 3)
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (3, "系统")

    def test_shows_remote_when_superseding(self, layered):
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install()
        assert describe_entity_version(
            f"scenes/{_SCENE}.yaml") == (4, "远程下发")

    def test_shows_local_when_locally_overridden(self, layered):
        system, local, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        write_scene(local, 1)
        install()
        assert describe_entity_version(
            f"scenes/{_SCENE}.yaml") == (1, "本地")

    def test_covers_both_scene_and_layout_files(self, layered):
        """场景定义与布局坐标能被远程**独立**顶替，只标一个就留下盲点。"""
        system, _, remote, write_scene, write_layout, install = layered
        write_scene(system, 1)
        write_layout(system, 2)
        write_layout(remote, 5)
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (1, "系统")
        assert describe_entity_version(
            f"layouts/{_LAYOUT}/{_SCENE}.json") == (5, "远程下发")

    def test_layout_omitted_when_no_layout_selected(self, layered):
        system, _, _, write_scene, write_layout, install = layered
        write_scene(system, 1)
        write_layout(system, 2)
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (1, "系统")

    def test_older_remote_does_not_show_as_remote(self, layered):
        """远程版本更旧时闸门不过，界面必须如实显示为系统。"""
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 9)
        write_scene(remote, 3)
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (9, "系统")

    def test_missing_scene_yields_empty_text(self, layered):
        *_, install = layered
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (None, "")

    def test_dev_mode_sees_remote_too(self, layered):
        """开发模式必须和用户看到同一份，否则复现不出用户的问题。"""
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install(dev_mode=True)
        assert describe_entity_version(
            f"scenes/{_SCENE}.yaml") == (4, "远程下发")


def test_version_link_only_stages_until_dialog_save(layered):
    system, _, _, write_scene, _, install = layered
    write_scene(system, 2)
    install(dev_mode=True)
    changes = []

    class PendingState:
        _scene_key = _SCENE
        _pending_scene_version = None
        _pending_layout_version = None
        on_version_pending_changed = changes.append

        @staticmethod
        def _version_rel_path(kind):
            assert kind == "scene"
            return f"scenes/{_SCENE}.yaml"

        @staticmethod
        def _refresh_version_info():
            pass

    state = PendingState()
    SceneTab._on_version_link(state, "scene")  # type: ignore[arg-type]

    assert state._pending_scene_version == 3
    assert versioning.read_version(
        system / "scenes" / f"{_SCENE}.yaml") == 2
    assert changes == [_SCENE]

    # 再次点击撤销待提升状态，磁盘仍保持原版本；Discard 同样不会有写盘入口。
    SceneTab._on_version_link(state, "scene")  # type: ignore[arg-type]
    assert state._pending_scene_version is None
    assert versioning.read_version(
        system / "scenes" / f"{_SCENE}.yaml") == 2
