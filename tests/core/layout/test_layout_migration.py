"""layout_manager 跨场景迁移测试

覆盖：
- migrate_layout_item 纯函数：region / point（箭头联动）/ panel 迁移
- LayoutConfigManager.migrate_item_across_layouts：全部布局文件迁移写盘
"""

import json

import pytest

from lvjiang.core.layout.manager import LayoutConfigManager, migrate_layout_item
from lvjiang.core.layout.models import Arrow, Layout, Panel, Point, Region
from lvjiang.core.paths import CONFIG_SUBDIRECTORIES


def _make_layout(name: str = "test") -> Layout:
    layout = Layout(key=name, name=name)
    layout.set_scene_regions("src", [
        Region("btn", 0.1, 0.2, 0.3, 0.4),
        Region("keep", 0.5, 0.5, 0.1, 0.1),
    ])
    layout.set_scene_points("src", [
        Point("origin", 0.4, 0.6),
        Point("stay", 0.7, 0.8),
    ])
    layout.set_scene_arrows("src", [
        Arrow("fwd", from_key="origin", to_cx_ratio=0.9, to_cy_ratio=0.1),
        Arrow("back", from_key="stay", to_key="origin"),
    ])
    layout.set_scene_panels("src", [
        Panel("grid", 0.0, 0.0, 0.5, 0.5, cols=6, rows=3),
    ])
    return layout


class TestMigrateLayoutItem:
    def test_region_migrated(self):
        layout = _make_layout()
        assert migrate_layout_item(layout, "src", "dst", "region", "btn") is True
        assert [r.key for r in layout.get_scene_regions("src")] == ["keep"]
        moved = layout.get_scene_regions("dst")
        assert [r.key for r in moved] == ["btn"]
        assert moved[0].x_ratio == 0.1

    def test_region_replaces_stale_target_entry(self):
        layout = _make_layout()
        layout.set_scene_regions("dst", [Region("btn", 0.9, 0.9, 0.1, 0.1)])
        assert migrate_layout_item(layout, "src", "dst", "region", "btn") is True
        dst = layout.get_scene_regions("dst")
        assert len(dst) == 1
        assert dst[0].x_ratio == 0.1  # 迁移项替换陈旧项

    def test_point_moves_with_from_arrow(self):
        layout = _make_layout()
        assert migrate_layout_item(layout, "src", "dst", "point", "origin") is True
        assert [p.key for p in layout.get_scene_points("src")] == ["stay"]
        assert [p.key for p in layout.get_scene_points("dst")] == ["origin"]
        # from_key == origin 的箭头随迁
        assert [a.key for a in layout.get_scene_arrows("dst")] == ["fwd"]

    def test_point_bakes_to_key_reference(self):
        layout = _make_layout()
        migrate_layout_item(layout, "src", "dst", "point", "origin")
        # source 中指向 origin 的箭头烘焙为绝对坐标
        remain = layout.get_scene_arrows("src")
        assert [a.key for a in remain] == ["back"]
        assert remain[0].to_key is None
        assert remain[0].to_cx_ratio == 0.4
        assert remain[0].to_cy_ratio == 0.6

    def test_moved_arrow_bakes_a_target_that_stays_behind(self):
        """随迁方向的终点若留在源场景，必须烘成绝对坐标。

        否则目标场景里凭空多出一条 `to_key` 指向不存在点的方向，静态检查
        在开跑前硬拦，用户还得回头手工修布局。
        """
        layout = _make_layout()
        layout.set_scene_arrows("src", [
            Arrow("linked", from_key="origin", to_key="stay"),
        ])

        assert migrate_layout_item(layout, "src", "dst", "point", "origin") is True

        moved = layout.get_scene_arrows("dst")
        assert [a.key for a in moved] == ["linked"]
        assert moved[0].to_key is None
        assert (moved[0].to_cx_ratio, moved[0].to_cy_ratio) == (0.7, 0.8)
        assert [p.key for p in layout.get_scene_points("src")] == ["stay"]

    def test_moved_self_loop_keeps_its_endpoint(self):
        """自环两端一起搬走，to_key 仍然有效，不该被烘死。"""
        layout = _make_layout()
        layout.set_scene_arrows("src", [
            Arrow("loop", from_key="origin", to_key="origin"),
        ])

        migrate_layout_item(layout, "src", "dst", "point", "origin")

        moved = layout.get_scene_arrows("dst")
        assert [a.key for a in moved] == ["loop"]
        assert moved[0].to_key == "origin"

    def test_panel_migrated(self):
        layout = _make_layout()
        assert migrate_layout_item(layout, "src", "dst", "panel", "grid") is True
        assert layout.get_scene_panels("src") == []
        dst = layout.get_scene_panels("dst")
        assert [p.key for p in dst] == ["grid"]
        assert dst[0].cols == 6

    def test_missing_key_no_change(self):
        layout = _make_layout()
        assert migrate_layout_item(layout, "src", "dst", "region", "nope") is False
        assert [r.key for r in layout.get_scene_regions("src")] == ["btn", "keep"]
        assert layout.get_scene_regions("dst") == []

    def test_unknown_kind_raises(self):
        layout = _make_layout()
        with pytest.raises(ValueError):
            migrate_layout_item(layout, "src", "dst", "arrow", "fwd")


class TestMigrateAcrossLayouts:
    @pytest.fixture
    def manager(self, tmp_path, monkeypatch):
        """resolver 三层根指向 tmp_path 的独立管理器（开发模式写 system）"""
        import lvjiang.constants as constants
        import lvjiang.core.config.resolver as cr
        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "system", tmp_path / "system")
        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "local", tmp_path / "local")
        monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        # 重置 resolver 单例以使用新路径
        monkeypatch.setattr(cr, "_resolver", None)
        mgr = LayoutConfigManager()
        for name in ("layout_a", "layout_b"):
            mgr.save_layout(_make_layout(name))
        # 无关布局：不含待迁移 key
        empty = Layout(key="layout_c", name="layout_c")
        empty.set_scene_regions("src", [Region("keep", 0.5, 0.5, 0.1, 0.1)])
        mgr.save_layout(empty)
        return mgr

    def test_all_layouts_migrated(self, manager):
        changed = manager.migrate_item_across_layouts("src", "dst", "region", "btn")
        assert changed == ["layout_a", "layout_b"]
        for name in ("layout_a", "layout_b"):
            layout = manager.load_layout(name)
            assert [r.key for r in layout.get_scene_regions("src")] == ["keep"]
            assert [r.key for r in layout.get_scene_regions("dst")] == ["btn"]

    def test_unrelated_layout_untouched(self, manager):
        from lvjiang.core.config.resolver import get_resolver
        # 读取layout_c 场景文件的当前内容
        path = get_resolver().resolve_read("layouts/layout_c/src.json")
        assert path is not None
        before = path.read_text(encoding="utf-8")
        manager.migrate_item_across_layouts("src", "dst", "region", "btn")
        assert path.read_text(encoding="utf-8") == before
        layout = manager.load_layout("layout_c")
        assert [r.key for r in layout.get_scene_regions("src")] == ["keep"]

    def test_point_migration_persists_arrows(self, manager):
        manager.migrate_item_across_layouts("src", "dst", "point", "origin")
        layout = manager.load_layout("layout_a")
        assert [a.key for a in layout.get_scene_arrows("dst")] == ["fwd"]
        remain = layout.get_scene_arrows("src")
        assert remain[0].to_key is None
        assert remain[0].to_cx_ratio == 0.4


class TestDeleteAcrossLayouts:
    """删定义时清掉各套布局里的坐标，避免残留。

    加载期的孤儿清理只清内存：没打开过的布局文件里那条记录会一直躺着，
    直到有人恰好加载并保存了那套布局。更糟的是同名重建——旧坐标会被当成
    新实体的坐标，位置莫名其妙还完全静默。
    """

    @pytest.fixture
    def manager(self, tmp_path, monkeypatch):
        import lvjiang.constants as constants
        import lvjiang.core.config.resolver as cr
        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "system", tmp_path / "system")
        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "local", tmp_path / "local")
        monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        monkeypatch.setattr(cr, "_resolver", None)
        mgr = LayoutConfigManager()
        for name in ("layout_a", "layout_b"):
            mgr.save_layout(_make_layout(name))
        empty = Layout(key="layout_c", name="layout_c")
        empty.set_scene_regions("src", [Region("keep", 0.5, 0.5, 0.1, 0.1)])
        mgr.save_layout(empty)
        return mgr

    def _scene_json(self, layout_name: str) -> dict:
        import json

        from lvjiang.core.config.resolver import get_resolver
        path = get_resolver().resolve_read(f"layouts/{layout_name}/src.json")
        assert path is not None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_region_is_removed_from_every_layout(self, manager):
        from lvjiang.core.layout.manager import delete_item_key_across_all_layouts

        changed = delete_item_key_across_all_layouts("src", "region", "btn")

        assert changed == ["layout_a", "layout_b"]
        for name in ("layout_a", "layout_b"):
            keys = [r["key"] for r in self._scene_json(name)["regions"]]
            assert keys == ["keep"]

    def test_deleting_a_point_takes_its_arrows_with_it(self, manager):
        """端点没了的 arrow 既画不出来也跑不了，留着就是下一条残留。"""
        from lvjiang.core.layout.manager import delete_item_key_across_all_layouts

        delete_item_key_across_all_layouts("src", "point", "origin")

        data = self._scene_json("layout_a")
        assert [p["key"] for p in data["points"]] == ["stay"]
        # fwd 的 from_key 与 back 的 to_key 都指向 origin，两条都该走
        assert data["arrows"] == []

    def test_a_layout_without_that_key_is_left_alone(self, manager):
        from lvjiang.core.config.resolver import get_resolver
        from lvjiang.core.layout.manager import delete_item_key_across_all_layouts

        path = get_resolver().resolve_read("layouts/layout_c/src.json")
        assert path is not None
        before = path.read_text(encoding="utf-8")

        assert "layout_c" not in delete_item_key_across_all_layouts(
            "src", "region", "btn")
        assert path.read_text(encoding="utf-8") == before

    def test_panels_are_covered_too(self, manager):
        from lvjiang.core.layout.manager import delete_item_key_across_all_layouts

        delete_item_key_across_all_layouts("src", "panel", "grid")

        assert self._scene_json("layout_a")["panels"] == []

    def test_an_unknown_kind_changes_nothing(self, manager):
        from lvjiang.core.layout.manager import delete_item_key_across_all_layouts

        assert delete_item_key_across_all_layouts("src", "arrow", "fwd") == []
        assert len(self._scene_json("layout_a")["arrows"]) == 2


class TestRenameCascade:
    """源实体/场景改名时，布局侧的坐标文件与引用位置都要跟过去。

    这两件事分别坏在不同的地方：坐标文件按「布局 + 分组 + 场景 key」定位，
    改完名之后按旧 key 推出来的路径根本不存在；``reference_positions`` 按
    (目标场景, 源场景, 源实体) 匹配，源地址一变就再也对不上。两种失配都静默：
    前者让改名后的场景在每套布局里都没有坐标，后者让用户摆好的位置回到源坐标。
    """

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        import json

        import yaml

        import lvjiang.constants as constants
        import lvjiang.core.config.resolver as cr
        import lvjiang.core.scene.registry as sr
        from lvjiang.core.scene.config import load_scene_manifest
        from lvjiang.core.scene.definition import SceneRegistry

        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "system", tmp_path / "system")
        monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "local", tmp_path / "local")
        monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        monkeypatch.setattr(cr, "_resolver", None)

        scenes = tmp_path / "system" / "scenes"
        (scenes / "general").mkdir(parents=True)
        (scenes / "general" / "src.yaml").write_text(yaml.safe_dump({
            "key": "src", "name": "源",
            "regions": [{"key": "close_btn", "name": "关闭", "type": "func"}],
        }, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (scenes / "general" / "dst.yaml").write_text(yaml.safe_dump({
            "key": "dst", "name": "目标",
            "references": [{"scene": "src", "entity": "close_btn"}],
        }, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (scenes / "general.yaml").write_text(
            yaml.safe_dump({"name": "通用"}, allow_unicode=True), encoding="utf-8")
        (scenes / "_meta.json").write_text(json.dumps(
            {"groups": [{"key": "general", "scenes": ["src", "dst"]}]},
            ensure_ascii=False), encoding="utf-8")

        resolver = cr.get_resolver()
        manifest = load_scene_manifest(resolver)
        registry = SceneRegistry(
            resolver=resolver, scene_order=manifest.order,
            group_config=manifest.groups, group_names=manifest.group_names)
        # 布局路径解析（_scene_group_of）读的是模块单例，替身到本用例的场景表
        monkeypatch.setattr(sr, "_registry", registry)

        layout = Layout(key="test_layout", name="测试布局")
        layout.set_scene_regions("src", [Region("close_btn", 0.1, 0.1, 0.2, 0.2)])
        layout.set_scene_regions("dst", [
            Region("close_btn", 0.9, 0.9, 0.2, 0.2, source_scene="src",
                   position_overridden=True,
                   source_x_ratio=0.1, source_y_ratio=0.1)])
        assert LayoutConfigManager().save_layout(layout)
        return registry, tmp_path / "system" / "layouts" / "test_layout" / "general"

    def test_renaming_the_source_entity_carries_the_custom_position(self, env):
        registry, scene_dir = env
        dst_json = scene_dir / "dst.json"
        rows = json.loads(dst_json.read_text(encoding="utf-8"))["reference_positions"]
        assert [(r["scene"], r["entity"]) for r in rows] == [("src", "close_btn")]

        registry.rename_region_key("src", "close_btn", "ok_btn")

        rows = json.loads(dst_json.read_text(encoding="utf-8"))["reference_positions"]
        assert [(r["scene"], r["entity"]) for r in rows] == [("src", "ok_btn")]
        assert (rows[0]["x_ratio"], rows[0]["y_ratio"]) == (0.9, 0.9)

    def test_renaming_the_scene_moves_its_coords_and_retargets_positions(self, env):
        registry, scene_dir = env
        assert (scene_dir / "src.json").exists()

        registry.rename_scene("src", "src2", "源二")

        assert not (scene_dir / "src.json").exists()
        moved = json.loads(
            (scene_dir / "src2.json").read_text(encoding="utf-8"))
        assert [r["key"] for r in moved["regions"]] == ["close_btn"]
        rows = json.loads(
            (scene_dir / "dst.json").read_text(encoding="utf-8"))["reference_positions"]
        assert [(r["scene"], r["entity"]) for r in rows] == [("src2", "close_btn")]

