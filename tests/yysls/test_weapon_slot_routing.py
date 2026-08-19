from lvjiang.apps.yysls.ui.loadout.equip.status_tab import _route_weapon_slot


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
