"""场景编辑器版本控件的来源与版本数据。

远程下发的配置会顶替系统文件。不标出来的话，开发者本地跑出来的行为和用户
不一样却毫不知情，用户报"识别坏了"时根本复现不出——而这恰恰是在线下发最
需要被排查的一类问题。

主要测试「解析层 + 文案层」这对纯函数而不是 SceneTab 控件：构造 SceneTab
要拉起整个控件树，在测试里反复创建/析构会触发 item delegate 的析构时序
问题（PyQt 已知坑，与本功能无关，HEAD 上同样复现）。
"""
from __future__ import annotations

import pytest

import lvjiang.core.config.resolver as resolver_mod
from lvjiang.core.config import versioning
from lvjiang.core.config.resolver import EntityOrigin
from lvjiang.ui.config_origin import layer_label
from lvjiang.ui.scene_editor.scene_tab import SceneTab


def describe_entity_version(rel_path: str) -> tuple[int | None, str]:
    """版本控件展示的两项：当前生效版本 + 来源文案"""
    origin = resolver_mod.get_resolver().describe_entity(rel_path)
    return origin.version, layer_label(origin.layer)

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
            f"scenes/{_SCENE}.yaml") == (4, "远程")

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
            f"layouts/{_LAYOUT}/{_SCENE}.json") == (5, "远程")

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

    def test_missing_scene_reports_unknown(self, layered):
        """解析不到任何一层时不能装作有来源，如实报未知"""
        *_, install = layered
        install()
        assert describe_entity_version(f"scenes/{_SCENE}.yaml") == (None, "未知")

    def test_dev_mode_sees_remote_too(self, layered):
        """开发模式必须和用户看到同一份，否则复现不出用户的问题。"""
        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install(dev_mode=True)
        assert describe_entity_version(
            f"scenes/{_SCENE}.yaml") == (4, "远程")


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


# ─── 悬停说明 ────────────────────────────────────────────

class TestOriginTooltip:
    """「这份配置来自哪一层」必须悬停就能看到，而且三个控件都能触发。

    只给数值挂 tooltip 等于没做：多数人会把鼠标停在「场景版本：」这几个
    字上，什么也不出来就以为没有说明。
    """

    def test_shows_current_and_every_existing_version_without_path(self, layered):
        from lvjiang.ui.config_origin import origin_tooltip

        system, _, remote, write_scene, _, install = layered
        write_scene(system, 1)
        write_scene(remote, 4)
        install()
        rel = f"scenes/{_SCENE}.yaml"
        resolver = resolver_mod.get_resolver()
        tip = origin_tooltip(
            resolver.describe_entity(rel), resolver.list_entity_origins(rel))
        assert "当前生效：远程 · v4" in tip
        assert "现有版本：2 份，来自 2 个来源" in tip
        assert "系统 · v1" in tip
        assert rel not in tip

    def test_pending_listed_separately_from_current(self, layered):
        """待保存的提升目标不能混进「当前版本」，否则以为盘上已经是那个号"""
        from lvjiang.ui.config_origin import origin_tooltip

        system, _, _, write_scene, _, install = layered
        write_scene(system, 2)
        install(dev_mode=True)
        tip = origin_tooltip(
            EntityOrigin("system", 2), (EntityOrigin("system", 2),), pending=3)
        assert "当前生效：系统 · v2" in tip
        assert "v3" in tip and "保存" in tip

    def test_all_three_widgets_share_the_tooltip(self, layered, qtbot):
        system, _, _, write_scene, write_layout, install = layered
        write_scene(system, 1)
        write_layout(system, 2)
        install(dev_mode=True)
        tab = SceneTab(_SCENE)
        qtbot.addWidget(tab)
        tab.set_layout_name(_LAYOUT)
        tip = tab._scene_version_value.toolTip()
        assert tip
        assert tab._scene_version_title.toolTip() == tip
        assert tab._scene_version_link.toolTip() == tip
        assert tab._layout_version_title.toolTip() == tab._layout_version_value.toolTip()
        assert f"layouts/{_LAYOUT}/{_SCENE}.json" not in (
            tab._layout_version_title.toolTip())


# ─── 保存后的「未生效」提示 ──────────────────────────────

class TestSystemSaveOverrideHint:
    """开发模式写 system 后，若 local/remote 仍生效就必须如实提示。
    """

    def _mixin(self):
        from lvjiang.ui.scene_editor.layout_ops import LayoutOpsMixin

        return LayoutOpsMixin()

    def test_reports_scene_whose_layout_is_still_remote(self, layered):
        system, _, remote, _, write_layout, install = layered
        write_layout(system, 2)
        write_layout(remote, 5)
        install(dev_mode=True)
        overrides = self._mixin()._system_save_overrides(_LAYOUT, {_SCENE})
        assert (overrides[_SCENE].layer, overrides[_SCENE].version) == (
            "remote", 5)

    def test_reports_scene_whose_layout_is_still_local(self, layered):
        system, local, _, _, write_layout, install = layered
        write_layout(system, 2)
        write_layout(local, 2)
        install(dev_mode=True)
        overrides = self._mixin()._system_save_overrides(_LAYOUT, {_SCENE})
        assert overrides[_SCENE].layer == "local"

    def test_silent_when_system_wins(self, layered):
        system, _, remote, _, write_layout, install = layered
        write_layout(system, 9)
        write_layout(remote, 3)          # 线上更旧，闸门不过
        install(dev_mode=True)
        assert self._mixin()._system_save_overrides(_LAYOUT, {_SCENE}) == {}

    def test_user_mode_never_reports(self, layered):
        """用户模式写 local 影子，恒为最高优先级，不存在被顶替的问题"""
        system, _, remote, _, write_layout, install = layered
        write_layout(system, 2)
        write_layout(remote, 5)
        install(dev_mode=False)
        assert self._mixin()._system_save_overrides(_LAYOUT, {_SCENE}) == {}

    def test_only_looks_at_scenes_this_save_wrote(self, layered):
        system, _, remote, _, write_layout, install = layered
        write_layout(system, 2)
        write_layout(remote, 5)
        install(dev_mode=True)
        assert self._mixin()._system_save_overrides(_LAYOUT, set()) == {}

    def test_resolves_layout_paths_once_for_all_written_scenes(
            self, layered, monkeypatch):
        import lvjiang.ui.scene_editor.layout_ops as layout_ops

        system, _, _, _, write_layout, install = layered
        write_layout(system, 2)
        install(dev_mode=True)
        original = layout_ops.scene_layout_rels
        calls = []

        def counted(layout_name, scene_keys):
            calls.append(tuple(scene_keys))
            return original(layout_name, scene_keys)

        monkeypatch.setattr(layout_ops, "scene_layout_rels", counted)
        self._mixin()._system_save_overrides(_LAYOUT, {_SCENE, "other"})
        assert calls == [("activity_main", "other")]
