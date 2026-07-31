"""layout_manager 跨场景迁移测试

覆盖：
- migrate_layout_item 纯函数：region / point（箭头联动）/ panel 迁移
- LayoutConfigManager.migrate_item_across_layouts：全部布局文件迁移写盘
"""

import pytest

from lvjiang.core import layout_manager
from lvjiang.core.layout_manager import LayoutConfigManager, migrate_layout_item
from lvjiang.core.scene_registry import Arrow, Layout, Panel, Point, Region


def _make_layout(name: str = "test") -> Layout:
    layout = Layout(name=name)
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
        monkeypatch.setattr(constants, "SYSTEM_CONFIG_DIR", tmp_path / "system")
        monkeypatch.setattr(constants, "LOCAL_CONFIG_DIR", tmp_path / "local")
        monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
        monkeypatch.setattr(layout_manager, "SESSION_CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        # 重置 resolver 单例以使用新路径
        import lvjiang.core.config_resolver as cr
        monkeypatch.setattr(cr, "_resolver", None)
        mgr = LayoutConfigManager()
        for name in ("布局A", "布局B"):
            mgr.save_layout(_make_layout(name))
        # 无关布局：不含待迁移 key
        empty = Layout(name="布局C")
        empty.set_scene_regions("src", [Region("keep", 0.5, 0.5, 0.1, 0.1)])
        mgr.save_layout(empty)
        return mgr

    def test_all_layouts_migrated(self, manager):
        changed = manager.migrate_item_across_layouts("src", "dst", "region", "btn")
        assert changed == ["布局A", "布局B"]
        for name in ("布局A", "布局B"):
            layout = manager.load_layout(name)
            assert [r.key for r in layout.get_scene_regions("src")] == ["keep"]
            assert [r.key for r in layout.get_scene_regions("dst")] == ["btn"]

    def test_unrelated_layout_untouched(self, manager):
        from lvjiang.core.config_resolver import get_resolver
        # 读取布局C 场景文件的当前内容
        path = get_resolver().resolve_read("layouts/布局C/src.json")
        assert path is not None
        before = path.read_text(encoding="utf-8")
        manager.migrate_item_across_layouts("src", "dst", "region", "btn")
        assert path.read_text(encoding="utf-8") == before
        layout = manager.load_layout("布局C")
        assert [r.key for r in layout.get_scene_regions("src")] == ["keep"]

    def test_point_migration_persists_arrows(self, manager):
        manager.migrate_item_across_layouts("src", "dst", "point", "origin")
        layout = manager.load_layout("布局A")
        assert [a.key for a in layout.get_scene_arrows("dst")] == ["fwd"]
        remain = layout.get_scene_arrows("src")
        assert remain[0].to_key is None
        assert remain[0].to_cx_ratio == 0.4
