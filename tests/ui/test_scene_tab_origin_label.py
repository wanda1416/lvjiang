"""场景编辑器的「版本号 / 来源」文案。

远端下发的配置会顶替出厂文件。不标出来的话，开发者本地跑出来的行为和用户
不一样却毫不知情，用户报"识别坏了"时根本复现不出——而这恰恰是在线下发最
需要被排查的一类问题。

测的是 `describe_scene_origin` 纯函数而不是 SceneTab 控件：构造 SceneTab
要拉起整个控件树，在测试里反复创建/析构会触发 item delegate 的析构时序
问题（PyQt 已知坑，与本功能无关，HEAD 上同样复现）。
"""
from __future__ import annotations

import pytest

import lvjiang.core.config.resolver as resolver_mod
from lvjiang.ui.scene_editor.scene_tab import describe_scene_origin

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
        assert describe_scene_origin(_SCENE) == ("场景 v3·系统", False)

    def test_shows_remote_when_superseding(self, layered):
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install()
        assert describe_scene_origin(_SCENE) == ("场景 v4·远端", True)

    def test_shows_user_when_locally_overridden(self, layered):
        system, local, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        write_scene(local, 1)
        install()
        text, has_remote = describe_scene_origin(_SCENE)
        assert "用户" in text and has_remote is False

    def test_covers_both_scene_and_layout_files(self, layered):
        """场景定义与布局坐标能被远端**独立**顶替，只标一个就留下盲点。"""
        system, _, remote, write_scene, write_layout, install = layered
        write_scene(system, 1)
        write_layout(system, 2)
        write_layout(remote, 5)
        install()
        text, has_remote = describe_scene_origin(_SCENE, _LAYOUT)
        assert "场景 v1·系统" in text
        assert "布局 v5·远端" in text
        assert has_remote is True

    def test_layout_omitted_when_no_layout_selected(self, layered):
        system, _, _, write_scene, write_layout, install = layered
        write_scene(system, 1)
        write_layout(system, 2)
        install()
        assert "布局" not in describe_scene_origin(_SCENE)[0]

    def test_older_remote_does_not_show_as_remote(self, layered):
        """远端版本更旧时闸门不过，界面必须如实显示为系统。"""
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 9)
        write_scene(remote, 3)
        install()
        assert describe_scene_origin(_SCENE) == ("场景 v9·系统", False)

    def test_missing_scene_yields_empty_text(self, layered):
        *_, install = layered
        install()
        assert describe_scene_origin(_SCENE) == ("", False)

    def test_dev_mode_sees_remote_too(self, layered):
        """开发模式必须和用户看到同一份，否则复现不出用户的问题。"""
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install(dev_mode=True)
        assert describe_scene_origin(_SCENE) == ("场景 v4·远端", True)
