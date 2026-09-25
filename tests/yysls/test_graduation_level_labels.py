"""等级口径要写进界面：毕业率挂模型等级，DPS 挂个人世界等级。

两者可以不一致——110 的社区表配 115 的角色正是现在的常态。不把等级写出来，
用户既看不出加载的是哪一份表，也会以为软件把数算错了。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLabel

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.graduation.model_registry import (
    select_graduation_model,
)
from lvjiang.apps.yysls.core.loadout import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel


@pytest.fixture
def gc():
    return get_game_config()


def _state(tmp_path, gc, *, world_level=None):
    repo = LoadoutRepository("labels-user", tmp_path)
    repo.create_plan("方案", "无名剑法", "无名枪法", activate=True)
    if world_level is not None:
        repo.set_world_level(world_level)
    state = repo.load()
    plan = state.active_plan
    plan.graduation_scheme = "基础方案"
    return state


def _labels(state, school, gc):
    fake = SimpleNamespace(
        _metric_dps_name=QLabel(), _metric_rate_name=QLabel())
    LoadoutPanel._refresh_metric_labels(fake, state, school, gc)
    return fake._metric_dps_name.text(), fake._metric_rate_name.text()


def test_dps_carries_the_personal_world_level(tmp_path, gc, qtbot):
    """DPS 是过完该等级抗性后的绝对值，挂个人世界等级。"""
    state = _state(tmp_path, gc)
    season = gc.current_equip_level()

    dps, _rate = _labels(state, "鸣金·虹", gc)

    assert dps == f"{season}级·DPS"


def test_graduation_carries_the_model_level(tmp_path, gc, qtbot):
    """毕业率衡量「相对这一档的最大 DPS 还差多少」，挂模型标定等级。

    只挂等级不挂版本：这一行与 DPS 并排，风格要一致；版本在分析对话框写全。
    """
    state = _state(tmp_path, gc)
    model = select_graduation_model(
        "鸣金·虹", "基础方案", gc.current_equip_level())
    assert model is not None, "没有可用模型，本用例失去意义"

    _dps, rate = _labels(state, "鸣金·虹", gc)

    assert rate == f"{model.level}级·毕业率"
    assert "v" not in rate


def test_two_labels_may_disagree(tmp_path, gc, qtbot):
    """115 的角色配 110 的表：两个等级本来就不同，不能互相顶替。"""
    state = _state(tmp_path, gc)

    dps, rate = _labels(state, "鸣金·虹", gc)

    assert dps.startswith(f"{gc.current_equip_level()}级")
    assert rate.startswith("110级")


def test_labels_fall_back_when_nothing_resolves(tmp_path, gc, qtbot):
    """世界等级低于所有模型时毕业率没有等级可挂，回落成裸标签。"""
    state = _state(tmp_path, gc)
    low = copy.deepcopy(state)
    low.world_level = 105

    dps, rate = _labels(low, "鸣金·虹", gc)

    assert dps == "105级·DPS"
    assert rate == "毕业率"


def test_unknown_school_keeps_bare_labels(tmp_path, gc, qtbot):
    state = _state(tmp_path, gc)

    _dps, rate = _labels(state, "", gc)

    assert rate == "毕业率"
