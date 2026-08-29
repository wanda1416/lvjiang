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
from lvjiang.core.layout_models import (
    Arrow,
    CanvasConfig,
    Layout,
    Panel,
    Point,
    Region,
)


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
    import lvjiang.core.config.resolver as cr

    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
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

    def test_user_mode_refuses_system_layout(self, env, monkeypatch):
        """系统布局属于 system 内容，用户模式下不可删除——不想用就别选它。"""
        import lvjiang.core.config.resolver as cr

        # 先在开发模式写 system
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("系统布局"))

        # 切换到用户模式
        monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
        monkeypatch.setattr(cr, "_resolver", None)
        mgr2 = LayoutConfigManager()

        assert mgr2.delete_layout("系统布局") is False
        assert "系统布局" in mgr2.list_layouts()
        assert not (env / "local" / "layouts" / "系统布局"
                    / "scene_a.json.deleted").exists()

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


class TestAliasLayout:
    """布局别名（extends）测试：scene 复用根布局，仅 canvas 独立"""

    @staticmethod
    def _add_alias_entry(env, alias: str, root: str, canvas: dict):
        """手工在 layouts.yaml 中追加别名条目（模拟用户编辑）"""
        import yaml
        yaml_path = env / "system" / "layouts.yaml"
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        doc["layouts"][alias] = {"extends": root, "canvas": canvas}
        yaml_path.write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def test_alias_loads_scenes_from_root(self, env):
        """别名加载：scene 来自根目录，canvas 取自身，name 为别名"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "别名布局", "根布局",
            {"x_ratio": 0.0, "y_ratio": 0.0, "w_ratio": 0.5, "h_ratio": 0.6})

        layout = load_layout_by_name("别名布局")
        assert layout is not None
        assert layout.name == "别名布局"
        # canvas 为自身条目值
        assert layout.canvas.w_ratio == pytest.approx(0.5)
        assert layout.canvas.h_ratio == pytest.approx(0.6)
        # scene 来自根布局
        assert [r.key for r in layout.get_scene_regions("scene_a")] == ["btn", "label"]
        assert [p.key for p in layout.get_scene_panels("scene_b")] == ["grid"]

    def test_alias_extends_missing_target_returns_none(self, env):
        """extends 指向不存在的布局 → None"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "坏别名", "不存在的根", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})
        assert load_layout_by_name("坏别名") is None

    def test_alias_multi_level_inheritance_returns_none(self, env):
        """extends 指向另一个带 extends 的布局（多级继承）→ None"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "一级别名", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})
        self._add_alias_entry(
            env, "二级别名", "一级别名", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})
        # 一级别名正常加载
        assert load_layout_by_name("一级别名") is not None
        # 二级别名被禁止
        assert load_layout_by_name("二级别名") is None

    def test_alias_save_preserves_extends_and_writes_root_dir(self, env):
        """save_layout 别名：yaml 保留 extends + 更新 canvas；scene 写根目录"""
        import yaml
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "别名布局", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})

        # 加载别名 → 修改 → 保存
        layout = load_layout_by_name("别名布局")
        layout.canvas = CanvasConfig(x_ratio=0.2, y_ratio=0.3, w_ratio=0.7, h_ratio=0.8)
        layout.set_scene_regions("scene_a", [Region("newbtn", 0.1, 0.1, 0.2, 0.2)])
        mgr.save_layout(layout)

        # yaml 条目保留 extends + canvas 已更新
        doc = yaml.safe_load(
            (env / "system" / "layouts.yaml").read_text(encoding="utf-8"))
        alias_entry = doc["layouts"]["别名布局"]
        assert alias_entry["extends"] == "根布局"
        assert alias_entry["canvas"]["w_ratio"] == pytest.approx(0.7)

        # scene 写入根布局目录，别名目录不存在
        assert (env / "system" / "layouts" / "根布局" / "scene_a.json").exists()
        assert not (env / "system" / "layouts" / "别名布局").exists()
        # 根布局重新加载后包含新 region（单一事实源）
        root = load_layout_by_name("根布局")
        assert [r.key for r in root.get_scene_regions("scene_a")] == ["newbtn"]

    def test_alias_delete_only_removes_yaml_entry(self, env):
        """delete_layout 别名：仅移除 yaml 条目，根布局文件不受影响"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "别名布局", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})

        result = mgr.delete_layout("别名布局")
        assert result is True
        assert "别名布局" not in mgr.list_layouts()
        # 根布局完好
        root = load_layout_by_name("根布局")
        assert root is not None
        assert [r.key for r in root.get_scene_regions("scene_a")] == ["btn", "label"]

    def test_user_mode_refuses_system_alias_layout(self, env, monkeypatch):
        """别名没有场景目录，也必须按 system 的 layouts.yaml 名册保护。"""
        import lvjiang.core.config.resolver as cr

        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "系统别名", "根布局",
            {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1},
        )
        monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
        monkeypatch.setattr(cr, "_resolver", None)
        user_mgr = LayoutConfigManager()

        assert user_mgr.is_system_layout("系统别名")
        assert user_mgr.delete_layout("系统别名") is False
        assert "系统别名" in user_mgr.list_layouts()
        assert not (env / "local" / "layouts.yaml").exists()

    def test_new_layout_rejects_existing_alias_name(self, env):
        """new_layout 撞名别名 → ValueError，根布局不被清空"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "别名布局", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})

        with pytest.raises(ValueError):
            mgr.new_layout("别名布局")
        # 根布局数据完好
        root = load_layout_by_name("根布局")
        assert [r.key for r in root.get_scene_regions("scene_a")] == ["btn", "label"]

    def test_new_layout_rejects_existing_root_name(self, env):
        """new_layout 撞名普通布局 → ValueError"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        with pytest.raises(ValueError):
            mgr.new_layout("根布局")

    def test_delete_root_referenced_by_alias_rejected(self, env):
        """删除被别名引用的根布局 → 拒绝，别名不悬空"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "别名布局", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})

        result = mgr.delete_layout("根布局")
        assert result is False
        # 根布局与别名均可正常加载
        assert load_layout_by_name("根布局") is not None
        assert load_layout_by_name("别名布局") is not None

    def test_save_layout_rejects_invalid_extends(self, env):
        """save_layout 对非法 extends（多级继承）拒绝写盘"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        self._add_alias_entry(
            env, "一级别名", "根布局", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})
        self._add_alias_entry(
            env, "二级别名", "一级别名", {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1})

        # 直接构造内存布局尝试保存非法别名
        bad = _make_layout("二级别名")
        mgr.save_layout(bad)
        # 非法条目未被写入 scene（一级别名目录不存在）
        assert not (env / "system" / "layouts" / "一级别名").exists()
        assert not (env / "system" / "layouts" / "二级别名").exists()

    def test_create_alias_layout(self, env):
        """create_alias_layout：创建别名布局，仅 yaml 条目，无 scene 文件"""
        mgr = LayoutConfigManager()
        root = _make_layout("根布局")
        mgr.save_layout(root)

        canvas = CanvasConfig(x_ratio=0.1, y_ratio=0.2, w_ratio=0.8, h_ratio=0.9)
        alias = mgr.create_alias_layout("别名布局", "根布局", canvas)

        assert alias is not None
        assert alias.name == "别名布局"
        # canvas 为自身配置
        assert alias.canvas.w_ratio == pytest.approx(0.8)
        # scene 来自根布局
        assert [r.key for r in alias.get_scene_regions("scene_a")] == ["btn", "label"]
        # 别名目录不存在
        assert not (env / "system" / "layouts" / "别名布局").exists()
        # yaml 条目正确
        import yaml
        doc = yaml.safe_load((env / "system" / "layouts.yaml").read_text(encoding="utf-8"))
        assert doc["layouts"]["别名布局"]["extends"] == "根布局"

    def test_create_alias_layout_rejects_existing_name(self, env):
        """create_alias_layout：撞名已存在布局 → None"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        mgr.save_layout(_make_layout("已有布局"))
        canvas = CanvasConfig()
        assert mgr.create_alias_layout("已有布局", "根布局", canvas) is None

    def test_create_alias_layout_rejects_alias_target(self, env):
        """create_alias_layout：继承目标是别名 → None（禁止多级）"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        mgr.create_alias_layout("一级别名", "根布局", CanvasConfig())
        canvas = CanvasConfig()
        assert mgr.create_alias_layout("二级别名", "一级别名", canvas) is None


class TestSceneLayoutRel:
    """UI 靠这个路径解析场景坐标文件的来源层（system/remote/local）。

    别名布局（带 extends）的 scene 文件实际存放在**根布局**目录下，照别名
    名字拼路径会指向一个不存在的目录——调用方拿到的是"这个文件没有"的空
    结果而不自知：场景编辑器的来源标识就因此在继承布局下少显示了一半。
    """

    def test_root_layout_uses_its_own_dir(self, env):
        from lvjiang.core.layout_manager import scene_layout_rel
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        assert scene_layout_rel("根布局", "scene_a") == "layouts/根布局/scene_a.json"

    def test_alias_layout_points_at_root_dir(self, env):
        from lvjiang.core.layout_manager import scene_layout_rel
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        TestAliasLayout._add_alias_entry(
            env, "别名布局", "根布局",
            {"x_ratio": 0.0, "y_ratio": 0.0, "w_ratio": 0.5, "h_ratio": 0.6})
        rel = scene_layout_rel("别名布局", "scene_a")
        assert rel == "layouts/根布局/scene_a.json"
        # 而且这个路径确实存在——空结果正是原来的 bug
        assert (env / "system" / rel).exists()

    def test_unknown_layout_falls_back_to_its_own_name(self, env):
        """布局不存在时不该抛异常，退回按名字拼（调用方自会得到空来源）。"""
        from lvjiang.core.layout_manager import scene_layout_rel
        assert scene_layout_rel("没有的布局", "scene_a") == "layouts/没有的布局/scene_a.json"

    def test_batch_paths_parse_layout_registry_once(self, env, monkeypatch):
        from lvjiang.core.config.resolver import get_resolver
        from lvjiang.core.layout_manager import scene_layout_rels

        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"))
        resolver = get_resolver()
        original = resolver.load_merged
        calls = []

        def counted(rel_path):
            calls.append(rel_path)
            return original(rel_path)

        monkeypatch.setattr(resolver, "load_merged", counted)
        assert scene_layout_rels("根布局", ("scene_a", "scene_b")) == {
            "scene_a": "layouts/根布局/scene_a.json",
            "scene_b": "layouts/根布局/scene_b.json",
        }
        assert calls == ["layouts.yaml"]


class TestSaveLayoutScope:
    """写盘范围：空集不写任何场景，None 才是全量（新建/另存为用）。

    UI 侧曾在「没有脏场景」时传 None，于是"什么都没改、随手点一下保存"
    会把该布局全部场景写一遍——用户模式下即为每个场景生成 local 影子，
    而实体文件是整文件影子，等于一次点击把整个布局永久冻在本地。
    """

    def _saved_scene_files(self, env, name="根布局") -> set[str]:
        d = env / "system" / "layouts" / name
        return {p.name for p in d.glob("*.json")} if d.is_dir() else set()

    def test_empty_set_writes_no_scene(self, env):
        mgr = LayoutConfigManager()
        layout = _make_layout("根布局")
        mgr.save_layout(layout)                      # 先全量落一次
        before = self._saved_scene_files(env)
        assert before, "预期首次保存会写出场景文件"

        for path in (env / "system" / "layouts" / "根布局").glob("*.json"):
            path.unlink()
        mgr.save_layout(layout, changed_scenes=set())
        assert self._saved_scene_files(env) == set()

    def test_none_still_means_full_write(self, env):
        """新建/另存为依赖 None 的全量语义，不能一起改掉。"""
        mgr = LayoutConfigManager()
        mgr.save_layout(_make_layout("根布局"), changed_scenes=None)
        assert self._saved_scene_files(env)

    def test_subset_writes_only_that_scene(self, env):
        mgr = LayoutConfigManager()
        layout = _make_layout("根布局")
        mgr.save_layout(layout)
        for path in (env / "system" / "layouts" / "根布局").glob("*.json"):
            path.unlink()
        mgr.save_layout(layout, changed_scenes={"scene_a"})
        assert self._saved_scene_files(env) == {"scene_a.json"}

    def test_explicit_version_alone_writes_selected_scene(self, env):
        mgr = LayoutConfigManager()
        layout = _make_layout("根布局")
        mgr.save_layout(layout)
        scene_dir = env / "system" / "layouts" / "根布局"
        for path in scene_dir.glob("*.json"):
            path.unlink()

        mgr.save_layout(
            layout,
            changed_scenes=set(),
            content_versions={"scene_a": 2},
        )

        assert self._saved_scene_files(env) == {"scene_a.json"}
        data = json.loads((scene_dir / "scene_a.json").read_text(encoding="utf-8"))
        assert data["content_version"] == 2
