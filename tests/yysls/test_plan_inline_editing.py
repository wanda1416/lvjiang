"""方案管理表格的逐列编辑规则。

流派、两门武学和玩法不是三个独立字段：流派决定武学，武学决定玩法候选。改一列
要按这条链重算，而规则只有一份（core.loadout.plan_fields），表格和新建对话框
共用——两边各写一遍迟早漂移。
"""

from pathlib import Path

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.loadout import (
    LoadoutRepository,
    playstyle_options,
    resolve_school,
)
from lvjiang.apps.yysls.core.loadout.models import (
    COMBAT_TYPE_PVE,
    COMBAT_TYPE_PVP,
    LoadoutPlan,
)
from lvjiang.apps.yysls.ui.loadout.plan_table_delegate import (
    COL_COMBAT,
    COL_MAIN_ART,
    COL_NAME,
    COL_PLAYSTYLE,
    COL_SCHOOL,
    COL_SUB_ART,
    locked_columns,
    plan_field_updates,
)


@pytest.fixture
def schools():
    return get_game_config().get_schools()


def _school_pair(schools: dict) -> tuple[str, str, str]:
    """取一个真实流派及其预置的两门武学。"""
    for name, cfg in schools.items():
        main = str((cfg.get("main") or {}).get("martial_art") or "")
        sub = str((cfg.get("sub") or {}).get("martial_art") or "")
        if main and sub:
            return name, main, sub
    raise AssertionError("游戏配置里没有可用的流派")


#: 跨流派的武学组合，resolve_school 解析不出流派，属于「自定义」方案。
#: 用真实武学而不是编造的名字，这样玩法候选的计算也走真实配置。
CUSTOM_MAIN, CUSTOM_SUB = "无名剑法", "斩雪刀法"


def _plan(**kwargs) -> LoadoutPlan:
    base = dict(id="p1", name="方案", main_martial_art=CUSTOM_MAIN,
                sub_martial_art=CUSTOM_SUB)
    base.update(kwargs)
    return LoadoutPlan(**base)  # type: ignore[arg-type]


# ─── 可编辑性 ──────────────────────────────────────────────

def test_arts_are_locked_while_a_school_resolves(schools):
    """绑定了有效流派时武学由流派决定，不能单独改。"""
    school, main, sub = _school_pair(schools)
    assert resolve_school(main, sub, schools) == school

    locked = locked_columns(schools, _plan(
        main_martial_art=main, sub_martial_art=sub))

    assert locked == frozenset({COL_MAIN_ART, COL_SUB_ART})


def test_custom_arts_stay_editable(schools):
    """武学组合解析不出流派时是自定义方案，两列照常可改。"""
    assert locked_columns(schools, _plan()) == frozenset()


@pytest.mark.parametrize("column", [
    COL_NAME, COL_SCHOOL, COL_PLAYSTYLE, COL_COMBAT,
])
def test_other_columns_are_never_locked_by_a_school(schools, column):
    """锁的只有武学两列——名称、玩法、对战类型和流派无关。"""
    _school, main, sub = _school_pair(schools)
    locked = locked_columns(schools, _plan(
        main_martial_art=main, sub_martial_art=sub))

    assert column not in locked


# ─── 提交与联动 ────────────────────────────────────────────

def test_choosing_a_school_rewrites_both_arts(schools):
    """流派不是方案上的字段：选流派等于选它预置的那两门武学。"""
    school, main, sub = _school_pair(schools)

    updates = plan_field_updates(schools, _plan(), COL_SCHOOL, school)

    assert updates == {"main_martial_art": main, "sub_martial_art": sub}


def test_choosing_custom_school_leaves_arts_alone(schools):
    """切到「自定义」只是解锁武学两列，不该顺手改掉已有武学。"""
    assert plan_field_updates(schools, _plan(), COL_SCHOOL, "") == {}


def test_unknown_school_is_ignored(schools):
    """流派配置被删后表格里可能还留着旧值，不能据此把武学清空。"""
    assert plan_field_updates(
        schools, _plan(), COL_SCHOOL, "并不存在的流派") == {}


@pytest.mark.parametrize("column,value,field", [
    (COL_MAIN_ART, "无名刀法", "main_martial_art"),
    (COL_SUB_ART, "无名刀法", "sub_martial_art"),
    (COL_PLAYSTYLE, "某玩法", "playstyle"),
    (COL_COMBAT, COMBAT_TYPE_PVP, "combat_type"),
])
def test_simple_columns_write_their_own_field(schools, column, value, field):
    assert plan_field_updates(schools, _plan(), column, value) == {field: value}


def test_empty_name_is_rejected(schools):
    """名称不允许空串：返回空更新，调用方据此放弃写入并回滚展示。"""
    assert plan_field_updates(schools, _plan(), COL_NAME, "") == {}
    assert plan_field_updates(schools, _plan(), COL_NAME, "新名") == {
        "name": "新名"}


def test_empty_name_never_reaches_the_repository(tmp_path: Path):
    """端到端：提交空名称后仓储里的名字不变。"""
    from types import SimpleNamespace

    from lvjiang.apps.yysls.ui.loadout import plan_manager_dialog as module

    repo = LoadoutRepository("alice", tmp_path)
    plan = repo.create_plan("原名", "无名剑法", "无名枪法", activate=False)
    dialog = SimpleNamespace(
        _repo=lambda: repo,
        _game_config=get_game_config(),
        _mark_changed=lambda: None,
        _load_user=lambda **_kwargs: None,
    )

    module.PlanManagerDialog._commit_field(
        dialog, repo.load().plans[plan.id], COL_NAME, "   ")

    assert repo.load().plans[plan.id].name == "原名"


# ─── 玩法候选与对话框同源 ──────────────────────────────────

def test_playstyle_options_keep_an_unmatched_existing_choice():
    """已选玩法不在候选里、但武学没动过时必须保留并标注。

    少了这条，用户编辑别的字段（比如只改个名称）就会把玩法静默清空——而玩法
    配置本来就可能在方案建好之后被改过。
    """
    plan = _plan(playstyle="早就不匹配的玩法")
    options = playstyle_options(
        get_game_config(), plan.main_martial_art, plan.sub_martial_art,
        plan=plan)

    values = [value for _label, value in options]
    assert "早就不匹配的玩法" in values
    assert any("不匹配" in label for label, _v in options)


def test_playstyle_options_drop_the_old_choice_once_arts_change():
    """武学换了就是另一套方案，旧玩法不再保留。"""
    plan = _plan(playstyle="早就不匹配的玩法")
    options = playstyle_options(
        get_game_config(), "无名枪法", "十方破阵", plan=plan)

    assert "早就不匹配的玩法" not in [value for _label, value in options]


def test_playstyle_options_always_offer_an_empty_choice():
    options = playstyle_options(get_game_config(), CUSTOM_MAIN, CUSTOM_SUB)

    assert options[0][1] == ""


def test_combat_type_default_is_pve(schools):
    assert plan_field_updates(
        schools, _plan(), COL_COMBAT, COMBAT_TYPE_PVE) == {
            "combat_type": COMBAT_TYPE_PVE}


def test_plan_at_reuses_the_loaded_snapshot(qtbot, tmp_path, monkeypatch):
    """取行对应的方案不再读盘：委托每编辑一格要问三次，三次读盘是白花的。

    表格和快照在 _load_user 里一起重建，不会失配。
    """
    from lvjiang.apps.yysls.ui.loadout.plan_manager_dialog import (
        PlanManagerDialog,
    )

    repo = LoadoutRepository("alice", tmp_path)
    created = repo.create_plan("方案甲", CUSTOM_MAIN, CUSTOM_SUB,
                               activate=False)
    dialog = PlanManagerDialog(["alice"], "alice", tmp_path,
                               game_config=get_game_config())
    qtbot.addWidget(dialog)

    loads: list[str] = []
    original = LoadoutRepository.load
    monkeypatch.setattr(
        LoadoutRepository, "load",
        lambda self: (loads.append(self.username), original(self))[1])

    rows = [dialog._plan_at(row) for row in range(3)]

    assert loads == []
    assert rows[1] is not None and rows[1].id == created.id
    # 越界行返回 None，而不是抛 IndexError
    assert rows[2] is None
