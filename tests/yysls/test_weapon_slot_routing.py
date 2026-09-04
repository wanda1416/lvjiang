from types import SimpleNamespace

from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.equip.status_tab import (
    EquipStatusTab,
    _route_weapon_slot,
)


def test_route_to_main_when_matches_main_art_weapon():
    assert _route_weapon_slot("剑", "剑", "枪") == "main_weapon"


def test_route_to_sub_when_matches_sub_art_weapon():
    assert _route_weapon_slot("枪", "剑", "枪") == "sub_weapon"


def test_reject_weapon_matching_neither_art():
    assert _route_weapon_slot("刀", "剑", "枪") == "reject"


def test_ask_when_main_sub_weapon_same_type():
    # 主副武学武器相同：无法自动判定，仍需手动选择
    assert _route_weapon_slot("剑", "剑", "剑") == "ask"


def test_ask_when_school_not_bound():
    # 流派未绑定（武器类型未知）：退回手动选择
    assert _route_weapon_slot("剑", "", "枪") == "ask"
    assert _route_weapon_slot("剑", "剑", "") == "ask"


def test_weapon_slots_follow_plan_art_order(tmp_path, monkeypatch):
    """交换方案中的两门武学后，武器槽位也必须跟随交换。"""
    import lvjiang.apps.yysls.config as game_config_module
    import lvjiang.constants

    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)

    class _GameConfig:
        @staticmethod
        def get_martial_art_weapon(name):
            return {"剑法": "剑", "刀法": "刀"}.get(name, "")

    monkeypatch.setattr(
        game_config_module, "get_game_config", lambda: _GameConfig())

    repo = LoadoutRepository("alice", tmp_path)
    plan_id = repo.load().active_plan_id
    repo.configure_plan(
        plan_id,
        main_martial_art="刀法",
        sub_martial_art="剑法",
    )
    tab = SimpleNamespace(
        _host=SimpleNamespace(active_user_name=lambda: "alice")
    )

    assert EquipStatusTab._get_plan_weapon_type(tab, "main_weapon") == "刀"
    assert EquipStatusTab._get_plan_weapon_type(tab, "sub_weapon") == "剑"
