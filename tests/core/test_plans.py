"""方案（Plan）核心存储与判定测试。

只测 session.json 那一层；app.yaml 的分发层由根 conftest 的
``distributed_plans_store``（autouse）统一挡掉。
"""
from __future__ import annotations

from lvjiang.core.config.plans import (
    PLAN_MODE_ADB,
    PLAN_MODE_WINDOW,
    Plan,
    get_active_plan,
    get_active_plan_id,
    load_plans,
    save_plans,
    set_active_plan_id,
)
from lvjiang.core.config.session import (
    get_session_store,
    load_settings,
    save_env,
    save_settings,
)


def _plan(name: str, **kwargs) -> Plan:
    return Plan.create(name, **kwargs)


def test_save_and_load_round_trip():
    plan = _plan("端游", space="端游", env="desktop", layout="桌面布局",
                 modes=[PLAN_MODE_WINDOW])

    save_plans([plan])

    loaded = load_plans()
    assert [p.to_dict() for p in loaded] == [plan.to_dict()]


def test_create_defaults_to_no_mode_restriction():
    plan = _plan("随便")

    assert plan.modes == []
    assert plan.allows(PLAN_MODE_WINDOW)
    assert plan.allows(PLAN_MODE_ADB)


def test_modes_are_deduped_and_ordered():
    plan = _plan("模拟器", modes=[PLAN_MODE_ADB, PLAN_MODE_WINDOW,
                                  PLAN_MODE_ADB, "不认识的模式"])

    assert plan.modes == [PLAN_MODE_WINDOW, PLAN_MODE_ADB]


def test_allows_rejects_unsupported_backend():
    plan = _plan("端游", modes=[PLAN_MODE_WINDOW])

    assert plan.allows(PLAN_MODE_WINDOW)
    assert not plan.allows(PLAN_MODE_ADB)


def test_allows_passes_when_backend_unknown():
    """未连接时 backend 为 None，放行——那属于「未连接」态，不归方案管。"""
    plan = _plan("端游", modes=[PLAN_MODE_WINDOW])

    assert plan.allows(None)
    assert plan.allows("")


def test_corrupt_modes_do_not_lock_the_user_out():
    """配置损坏成非法模式时按「不限制」处理，不能把用户挡在开始执行之外。"""
    get_session_store().set_node("plans", [
        {"id": "a", "name": "坏的", "modes": "windows"},
    ])

    plan = load_plans()[0]

    assert plan.modes == []
    assert plan.allows(PLAN_MODE_ADB)


def test_load_skips_malformed_entries_and_duplicate_ids():
    get_session_store().set_node("plans", [
        {"id": "a", "name": "好的"},
        {"id": "", "name": "缺 id"},
        {"id": "b"},
        "根本不是字典",
        {"id": "a", "name": "重复 id"},
    ])

    assert [p.name for p in load_plans()] == ["好的"]


def test_load_plans_tolerates_wrong_node_type():
    get_session_store().set_node("plans", {"不是": "列表"})

    assert load_plans() == []


def test_active_plan_id_defaults_to_custom():
    assert get_active_plan_id() == ""
    assert get_active_plan() is None


def test_active_plan_resolves_by_id():
    first = _plan("端游")
    second = _plan("模拟器")
    save_plans([first, second])

    set_active_plan_id(second.id)

    assert get_active_plan_id() == second.id
    active = get_active_plan()
    assert active is not None and active.name == "模拟器"


def test_active_plan_degrades_when_id_no_longer_exists():
    plan = _plan("端游")
    save_plans([plan])
    set_active_plan_id(plan.id)

    save_plans([])

    assert get_active_plan_id() == plan.id
    assert get_active_plan() is None


def test_rename_keeps_active_reference():
    """id 与 name 分离：改名不该让选中态失效。"""
    plan = _plan("端游")
    save_plans([plan])
    set_active_plan_id(plan.id)

    plan.name = "PC 端"
    save_plans([plan])

    active = get_active_plan()
    assert active is not None and active.name == "PC 端"


def test_active_plan_is_stored_under_actives():
    plan = _plan("端游")
    save_plans([plan])

    set_active_plan_id(plan.id)

    assert get_session_store().get_node("actives")["plan"] == plan.id


def test_save_env_keeps_sibling_settings():
    """save_env 曾用 load+set_node 整节点覆盖，会吃掉并发写入的兄弟键。"""
    save_settings({"font_sizes": {"base": 12}, "env": "desktop"})

    save_env("android")

    settings = load_settings()
    assert settings["env"] == "android"
    assert settings["font_sizes"] == {"base": 12}
