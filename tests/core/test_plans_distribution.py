"""分发方案：app.yaml 与 session.json 的双存储路由。"""
from __future__ import annotations

from lvjiang.core.config.plans import (
    PLAN_MODE_WINDOW,
    Plan,
    load_plans,
    save_plans,
)
from lvjiang.core.config.session import get_session_store


def _session_plans() -> list:
    return get_session_store().get_node("plans") or []


def test_distributed_plan_goes_to_app_yaml_only(distributed_plans_store):
    plan = Plan.create("预置端游", space="端游", env="desktop",
                       layout="桌面布局", modes=[PLAN_MODE_WINDOW],
                       distributed=True)

    save_plans([plan])

    assert [item["name"] for item in distributed_plans_store["plans"]] == ["预置端游"]
    assert _session_plans() == []


def test_local_plan_goes_to_session_only(distributed_plans_store):
    save_plans([Plan.create("本机", modes=[PLAN_MODE_WINDOW])])

    assert distributed_plans_store.get("plans", []) == []
    assert [item["name"] for item in _session_plans()] == ["本机"]


def test_distributed_flag_is_not_serialized(distributed_plans_store):
    """标志就是「存在哪」，落盘再存一份只会与实际位置脱节。"""
    save_plans([Plan.create("预置", distributed=True)])

    assert "distributed" not in distributed_plans_store["plans"][0]


def test_flipping_the_flag_moves_the_plan_between_stores(distributed_plans_store):
    plan = Plan.create("会搬家的方案", modes=[PLAN_MODE_WINDOW])
    save_plans([plan])
    assert len(_session_plans()) == 1

    plan.distributed = True
    save_plans([plan])

    assert [item["id"] for item in distributed_plans_store["plans"]] == [plan.id]
    assert _session_plans() == []

    plan.distributed = False
    save_plans([plan])

    assert distributed_plans_store["plans"] == []
    assert [item["id"] for item in _session_plans()] == [plan.id]


def test_load_merges_both_stores_with_distributed_first(distributed_plans_store):
    local = Plan.create("本机")
    shipped = Plan.create("预置", distributed=True)
    save_plans([local, shipped])

    loaded = load_plans()

    assert [p.name for p in loaded] == ["预置", "本机"]
    assert [p.distributed for p in loaded] == [True, False]


def test_distributed_flag_is_derived_from_where_it_was_found(distributed_plans_store):
    """app.yaml 条目即使没写标志，读回来也是分发方案。"""
    distributed_plans_store["plans"] = [{"id": "abc", "name": "预置"}]

    loaded = load_plans()

    assert [(p.name, p.distributed) for p in loaded] == [("预置", True)]


def test_same_id_in_both_stores_keeps_the_distributed_copy(distributed_plans_store):
    distributed_plans_store["plans"] = [{"id": "dup", "name": "分发版"}]
    get_session_store().set_node("plans", [{"id": "dup", "name": "本机版"}])

    loaded = load_plans()

    assert [(p.name, p.distributed) for p in loaded] == [("分发版", True)]


def test_corrupt_entries_in_app_yaml_are_skipped(distributed_plans_store):
    distributed_plans_store["plans"] = ["不是字典", {"name": "缺 id"}, {"id": "ok", "name": "好的"}]

    assert [p.name for p in load_plans()] == ["好的"]
