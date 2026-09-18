"""SchoolPanel 流派级转律词条库的加载与增删。"""

from copy import deepcopy

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel


def _make_panel(qtbot, schools: dict) -> tuple[SchoolPanel, list[dict]]:
    """注入数据的面板实例；on_changed 收集保存事件避免真实写盘。"""
    # 深拷贝：用例会原地修改流派配置，不能污染共享模板
    saved: list[dict] = []
    panel = SchoolPanel(data={"schools": deepcopy(schools)},
                        on_changed=lambda: saved.append(panel._data))
    qtbot.addWidget(panel)
    return panel, saved


_SCHOOLS = {
    "鸣金·虹": {
        "attr": "鸣金",
        "transmute_pool": ["最大外功攻击", "最大鸣金攻击", "会心率"],
    },
    "裂石·威": {
        "attr": "裂石",
        "transmute_pool": ["最大外功攻击", "最小外功攻击", "最大无相攻击",
                           "最大裂石攻击", "会心率", "敏", "劲"],
    },
}


def test_transmute_pool_shows_current_school(qtbot):
    panel, _saved = _make_panel(qtbot, dict(_SCHOOLS))
    panel._refresh_list(select="鸣金·虹")
    assert panel._pool_names() == ["最大外功攻击", "最大鸣金攻击", "会心率"]
    panel._refresh_list(select="裂石·威")
    assert panel._pool_names() == [
        "最大外功攻击", "最小外功攻击", "最大无相攻击",
        "最大裂石攻击", "会心率", "敏", "劲"]


def test_write_transmute_pool_persists_and_refreshes(qtbot):
    panel, saved = _make_panel(qtbot, dict(_SCHOOLS))
    panel._refresh_list(select="鸣金·虹")
    panel._write_transmute_pool("鸣金·虹", ["最大外功攻击", "势"])
    assert panel._pool_names() == ["最大外功攻击", "势"]
    assert panel._data["schools"]["鸣金·虹"]["transmute_pool"] == [
        "最大外功攻击", "势"]
    assert saved, "写入应触发保存回调"
    # 清空 → 省略键
    panel._write_transmute_pool("鸣金·虹", [])
    assert "transmute_pool" not in panel._data["schools"]["鸣金·虹"]
    assert panel._pool_names() == []


def test_pool_edit_dialog_writes_selection(qtbot, monkeypatch):
    """编辑对话框（AffixSelectSortDialog）返回的选择直接写入词条库。"""
    from lvjiang.apps.yysls.ui.game_settings import school_panel
    panel, _saved = _make_panel(qtbot, dict(_SCHOOLS))
    panel._refresh_list(select="鸣金·虹")

    # 模拟对话框返回新选择（追加了"势"）
    class _FakeDlg:
        def exec(self): return 1
        def selected(self): return ["最大外功攻击", "最大鸣金攻击", "会心率", "势"]
    monkeypatch.setattr(
        school_panel.AffixSelectSortDialog, "__init__",
        lambda self, *a, **k: None)
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "exec",
                        lambda self: _FakeDlg.exec(self))
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "selected",
                        lambda self: _FakeDlg.selected(self))
    panel._on_pool_edit()
    assert panel._pool_names() == [
        "最大外功攻击", "最大鸣金攻击", "会心率", "势"]


def test_pool_edit_dialog_cancel_keeps_old(qtbot, monkeypatch):
    """对话框取消时不写入任何变更。"""
    from lvjiang.apps.yysls.ui.game_settings import school_panel
    panel, saved_before = _make_panel(qtbot, dict(_SCHOOLS))
    panel._refresh_list(select="鸣金·虹")
    old_names = list(panel._pool_names())
    saved_count = len(saved_before)

    class _FakeDlg:
        def exec(self): return 0  # rejected
        def selected(self): return ["势"]
    monkeypatch.setattr(
        school_panel.AffixSelectSortDialog, "__init__",
        lambda self, *a, **k: None)
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "exec",
                        lambda self: _FakeDlg.exec(self))
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "selected",
                        lambda self: _FakeDlg.selected(self))
    panel._on_pool_edit()
    assert panel._pool_names() == old_names
    assert len(saved_before) == saved_count, "取消不应触发保存"


def test_pool_edit_remove_via_dialog(qtbot, monkeypatch):
    """对话框返回少了词条 → 等效于删除。"""
    from lvjiang.apps.yysls.ui.game_settings import school_panel
    panel, _saved = _make_panel(qtbot, dict(_SCHOOLS))
    panel._refresh_list(select="鸣金·虹")

    class _FakeDlg:
        def exec(self): return 1
        def selected(self): return ["最大外功攻击", "会心率"]
    monkeypatch.setattr(
        school_panel.AffixSelectSortDialog, "__init__",
        lambda self, *a, **k: None)
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "exec",
                        lambda self: _FakeDlg.exec(self))
    monkeypatch.setattr(school_panel.AffixSelectSortDialog, "selected",
                        lambda self: _FakeDlg.selected(self))
    panel._on_pool_edit()
    assert panel._pool_names() == ["最大外功攻击", "会心率"]


def test_game_config_transmute_pool_api():
    """GameConfigManager 的流派词库接口（真实配置）。"""
    from lvjiang.apps.yysls.config import get_game_config

    gc = get_game_config()
    # 鸣金：会意流派模板，含会意率/劲/势
    pool = gc.get_transmute_pool("鸣金·虹")
    assert pool[:3] == ["最大外功攻击", "最大无相攻击", "最大鸣金攻击"]
    assert "会意率" in pool and "劲" in pool and "势" in pool
    # 会心流派：无相 + 本属攻击并存
    pool_ls = gc.get_transmute_pool("裂石·威")
    assert "最大无相攻击" in pool_ls and "最大裂石攻击" in pool_ls
    assert "会心率" in pool_ls and "敏" in pool_ls and "劲" in pool_ls
    # 未配置/不存在的流派返回空列表
    assert gc.get_transmute_pool("不存在的流派") == []
    # 全量接口：配置内 11 个流派均有 7 词条
    all_pools = gc.get_all_transmute_pools()
    assert len(all_pools) == len(gc.get_schools())
    assert all(len(names) == 7 for names in all_pools.values())
