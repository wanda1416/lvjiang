"""最优组合的候选池装配：「排除模拟」开关必须真的能放模拟装备进来

背包侧的真实装备与模拟装备是两个独立集合（``EquipmentInventory.bag_items``
只含真实装备，模拟装备在 ``mock_items``）。候选池若只读 bag_items，
「排除模拟」勾不勾结果都一样——模拟装备根本没有机会参与毕业率计算。

只驱动 _load_candidates 本体：对话框 __init__ 要真实 host 与全套控件，
此处用 __new__ 装上该方法实际触碰的几个属性，并把 _SlotGroup 换成
记录用的替身，断言各槽位实际收到了哪些装备。
"""

import pytest
from PyQt6.QtWidgets import QScrollArea

import lvjiang.apps.yysls.ui.loadout.optimal_combo as mod


def _equip(name, type_, *, mock=False, level=110):
    fp = ("mock_" if mock else "") + name
    d = {
        "_fp": fp, "name": name, "type": type_, "level": level,
        "quality": "gold", "_extra": {},
    }
    if mock:
        d["_extra"]["is_mock"] = True
    return d


class _FakeInventory:
    """按 EquipmentInventory 的契约分开暴露真实/模拟两个集合"""

    def __init__(self, equipped, bag, mock):
        self.equipped = equipped
        self.bag_items = bag
        self.mock_items = mock


@pytest.fixture
def dialog(qtbot, monkeypatch):
    """装好 _load_candidates 所需最小属性的对话框壳"""
    from PyQt6.QtWidgets import QCheckBox, QLabel, QTabWidget

    captured: dict[str, list[dict]] = {}

    class _StubSlotGroup:
        def __init__(self, slot_key, _display, candidates, *_a, **_k):
            captured[slot_key] = list(candidates)
            self.rows = []          # 装完候选后要按候选评级过一遍勾选状态

    monkeypatch.setattr(mod, "_SlotGroup", _StubSlotGroup)

    dlg = mod.OptimalComboPage.__new__(mod.OptimalComboPage)
    dlg._school = "鸣金·虹"
    dlg._main_martial_art = ""
    dlg._sub_martial_art = ""
    dlg._level_threshold = 0
    dlg._affix_filter = ""
    dlg._slot_groups = {}
    dlg._slot_scroll_areas = {}
    dlg._chk_apply_tuning = QCheckBox()
    dlg._chk_apply_tuning.setChecked(True)
    qtbot.addWidget(dlg._chk_apply_tuning)
    dlg._chk_exclude_mock = QCheckBox()
    qtbot.addWidget(dlg._chk_exclude_mock)
    dlg._candidate_summary = QLabel()
    qtbot.addWidget(dlg._candidate_summary)
    dlg._tab_widget = QTabWidget()
    qtbot.addWidget(dlg._tab_widget)
    dlg._tab_widget.addTab(QLabel(), "候选装备")

    # 候选评级：不选玩法 = 不应用规则，候选池装配与它无关
    dlg._tuning_options = []
    dlg._tuning_selection = []

    class _Combo:
        def currentData(self):
            return "一般"
    dlg._combo_min_rating = _Combo()

    class _Host:
        def active_user_name(self):
            return "tester"
    dlg._host = _Host()

    dlg._captured = captured
    return dlg


def _install_inventory(monkeypatch, equipped, bag, mock):
    import lvjiang.apps.yysls.core.combat.equipment as eq_mod
    monkeypatch.setattr(
        eq_mod, "EquipmentInventory",
        lambda _user: _FakeInventory(equipped, bag, mock))


class TestExcludeMockSwitch:
    def test_mock_bag_items_enter_pool_when_not_excluded(
            self, dialog, monkeypatch):
        """未勾选排除：背包模拟装备必须进入候选池"""
        _install_inventory(
            monkeypatch,
            equipped={},
            bag={"head": {"h1": _equip("真冠", "冠胄")}},
            mock={"head": {"mock_h2": _equip("模冠", "冠胄", mock=True)}},
        )
        dialog._chk_exclude_mock.setChecked(False)
        dialog._load_candidates()
        names = [e["name"] for e in dialog._captured["head"]]
        assert "真冠" in names
        assert "模冠" in names

    def test_mock_bag_items_kept_out_when_excluded(self, dialog, monkeypatch):
        """勾选排除：模拟装备不得进入候选池"""
        _install_inventory(
            monkeypatch,
            equipped={},
            bag={"head": {"h1": _equip("真冠", "冠胄")}},
            mock={"head": {"mock_h2": _equip("模冠", "冠胄", mock=True)}},
        )
        dialog._chk_exclude_mock.setChecked(True)
        dialog._load_candidates()
        names = [e["name"] for e in dialog._captured["head"]]
        assert names == ["真冠"]

    def test_equipped_mock_follows_the_switch(self, dialog, monkeypatch):
        """已穿戴的模拟装备同样跟随开关：它只是模拟视图里的一条记录"""
        worn_mock = _equip("穿着的模冠", "冠胄", mock=True)
        _install_inventory(
            monkeypatch,
            equipped={"head": worn_mock},
            bag={}, mock={"head": {"mock_h1": dict(worn_mock)}},
        )
        dialog._chk_exclude_mock.setChecked(True)
        dialog._load_candidates()
        assert dialog._captured["head"] == []

        dialog._chk_exclude_mock.setChecked(False)
        dialog._load_candidates()
        assert [e["name"] for e in dialog._captured["head"]] == ["穿着的模冠"]

    def test_toggle_rebuilds_pool(self, dialog, monkeypatch):
        """开关本身要接上重建：候选池只在构造时加载一次，不接等于没有"""
        _install_inventory(
            monkeypatch,
            equipped={},
            bag={},
            mock={"head": {"mock_h2": _equip("模冠", "冠胄", mock=True)}},
        )
        dialog._chk_exclude_mock.setChecked(True)
        dialog._load_candidates()
        assert dialog._captured["head"] == []

        # 走真实的信号处理入口，而不是直接再调一次 _load_candidates
        dialog._on_exclude_mock_toggled(False)
        assert dialog._captured["head"] == []  # 开关还没变，池子仍不含模拟
        dialog._chk_exclude_mock.setChecked(False)
        dialog._on_exclude_mock_toggled(False)
        assert [e["name"] for e in dialog._captured["head"]] == ["模冠"]


def test_weapon_candidates_follow_plan_art_order(dialog, monkeypatch):
    """候选池的主副武器类型来自方案武学，而不是流派配置顺序。"""
    import lvjiang.apps.yysls.config as game_config_module

    class _GameConfig:
        @staticmethod
        def get_martial_art_weapon(name):
            return {"剑法": "剑", "刀法": "刀"}.get(name, "")

    monkeypatch.setattr(
        game_config_module, "get_game_config", lambda: _GameConfig())
    dialog._main_martial_art = "刀法"
    dialog._sub_martial_art = "剑法"
    _install_inventory(
        monkeypatch,
        equipped={},
        bag={"weapon": {
            "sword": _equip("剑", "剑"),
            "blade": _equip("刀", "刀"),
        }},
        mock={},
    )

    dialog._load_candidates()

    assert [e["name"] for e in dialog._captured["main_weapon"]] == ["刀"]
    assert [e["name"] for e in dialog._captured["sub_weapon"]] == ["剑"]


def test_slot_groups_fill_equal_regions_and_scroll_internally(qtbot):
    """候选数量不改变部位区域高度，超出部分由该区域自己滚动。"""
    scrolls = []
    groups = []
    for count in (2, 20):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.resize(260, 180)
        group = mod._SlotGroup(
            "head", "冠胄",
            [_equip(f"冠胄{i}", "冠胄") for i in range(count)],
            "鸣金·虹",
        )
        scroll.setWidget(group)
        qtbot.addWidget(scroll)
        scroll.show()
        scrolls.append(scroll)
        groups.append(group)

    qtbot.waitUntil(lambda: all(s.viewport().height() > 0 for s in scrolls))

    short_scroll, long_scroll = scrolls
    short_group, long_group = groups
    assert short_scroll.height() == long_scroll.height()
    assert short_group.height() == short_scroll.viewport().height()
    assert short_scroll.verticalScrollBar().maximum() == 0
    assert long_group.height() > long_scroll.viewport().height()
    assert long_scroll.verticalScrollBar().maximum() > 0
    # 多出的可见空间留在底部，首个候选行仍紧跟标题。
    assert short_group.rows[0].y() < short_group.height() // 2
