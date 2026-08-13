"""布局目录化存储测试

覆盖：
- save_layout → load_layout roundtrip（canvas + regions/points/arrows/panels）
- list_layouts 名册枚举
- delete_layout（开发模式直删 + 用户模式墓碑）
- load_layout_by_name 模块级函数
"""

import json

import pytest

from lvjiang.core import layout_manager
from lvjiang.core.layout_manager import LayoutConfigManager, load_layout_by_name
from lvjiang.core.scene_registry import Arrow, CanvasConfig, Layout, Panel, Point, Region


def _make_layout(name: str = "测试布局") -> Layout:
    layout = Layout(name=name)
    layout.canvas = CanvasConfig(x_ratio=0.1, y_ratio=0.2, w_ratio=0.8, h_ratio=0.9)
    layout.set_scene_regions("scene_a", [
        Region("btn", 0.1, 0.2, 0.3, 0.4),
        Region("label", 0.5, 0.5, 0.1, 0.1),
    ])
    layout.set_scene_points("scene_a", [
        Point("origin", 0.4, 0.6),
    ])
    layout.set_scene_arrows("scene_a", [
        Arrow("fwd", from_key="origin", to_cx_ratio=0.9, to_cy_ratio=0.1),
    ])
    layout.set_scene_panels("scene_b", [
        Panel("grid", 0.0, 0.0, 0.5, 0.5, cols=6, rows=3),
    ])
    return layout


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的三层配置环境（开发模式）"""
    import lvjiang.constants as constants
    import lvjiang.core.config_resolver as cr

    monkeypatch.setattr(constants, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(constants, "LOCAL_CONFIG_DIR", tmp_path / "local")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(layout_manager, "SESSION_CONFIG_DIR", tmp_path)
    monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
    monkeypatch.setattr(cr, "_resolver", None)
    return tmp_path


class TestSaveLoadRoundtrip:
    def test_roundtrip_preserves_all_data(self, env):
        mgr = LayoutConfigManager()
        original = _make_layout()
        mgr.save_layout(original)

        loaded = mgr.load_layout("测试布局")
        assert loaded is not None
        assert loaded.name == "测试布局"
        # canvas
        assert loaded.canvas.x_ratio == pytest.approx(0.1)
        assert loaded.canvas.y_ratio == pytest.approx(0.2)
        assert loaded.canvas.w_ratio == pytest.approx(0.8)
        assert loaded.canvas.h_ratio == pytest.approx(0.9)
        # regions
        regions = loaded.get_scene_regions("scene_a")
        assert [r.key for r in regions] == ["btn", "label"]
        assert regions[0].x_ratio == pytest.approx(0.1)
        # points
        points = loaded.get_scene_points("scene_a")
        assert [p.key for p in points] == ["origin"]
        # arrows
        arrows = loaded.get_scene_arrows("scene_a")
        assert [a.key for a in arrows] == ["fwd"]
        assert arrows[0].from_key == "origin"
        # panels
        panels = loaded.get_scene_panels("scene_b")
        assert [p.key for p in panels] == ["grid"]
        assert panels[0].cols == 6

    def test_scene_files_created_on_disk(self, env):
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout())
        scene_dir = env / "system" / "layouts" / "测试布局"
        assert scene_dir.is_dir()
        assert (scene_dir / "scene_a.json").exists()
        assert (scene_dir / "scene_b.json").exists()

    def test_layouts_yaml_created(self, env):
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout())
        yaml_path = env / "system" / "layouts.yaml"
        assert yaml_path.exists()
        import yaml
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert "测试布局" in doc["layouts"]
        assert doc["layouts"]["测试布局"]["canvas"]["w_ratio"] == pytest.approx(0.8)


class TestListLayouts:
    def test_list_from_yaml(self, env):
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("布局X"))
        mgr.save_layout(_make_layout("布局Y"))
        names = mgr.list_layouts()
        assert names == ["布局X", "布局Y"]

    def test_empty_when_no_layouts(self, env):
        mgr = LayoutConfigManager()
        assert mgr.list_layouts() == []


class TestDeleteLayout:
    def test_dev_mode_deletes_dir_and_yaml(self, env):
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("待删"))
        assert "待删" in mgr.list_layouts()

        result = mgr.delete_layout("待删")
        assert result is True
        assert "待删" not in mgr.list_layouts()
        assert mgr.load_layout("待删") is None
        # 目录已清
        assert not (env / "system" / "layouts" / "待删").exists()

    def test_delete_nonexistent_returns_false(self, env):
        mgr = LayoutConfigManager()
        assert mgr.delete_layout("不存在") is False

    def test_user_mode_tombstones(self, env, monkeypatch):
        """用户模式删除：system 布局落墓碑，yaml diff 删键"""
        import lvjiang.core.config_resolver as cr

        # 先在开发模式写 system
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("系统布局"))

        # 切换到用户模式
        monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
        monkeypatch.setattr(cr, "_resolver", None)
        mgr2 = LayoutConfigManager()

        result = mgr2.delete_layout("系统布局")
        assert result is True
        # 名册中不再出现
        assert "系统布局" not in mgr2.list_layouts()
        # 墓碑存在
        tomb = env / "local" / "layouts" / "系统布局" / "scene_a.json.deleted"
        assert tomb.exists()


class TestModuleLevelLoad:
    def test_load_layout_by_name(self, env):
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("模块级"))
        layout = load_layout_by_name("模块级")
        assert layout is not None
        assert layout.name == "模块级"
        assert [r.key for r in layout.get_scene_regions("scene_a")] == ["btn", "label"]

    def test_load_nonexistent_returns_none(self, env):
        assert load_layout_by_name("不存在") is None
