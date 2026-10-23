"""承音喂满所需的传律石估算（``core/affix_cultivation.py``）。

锁住的都是用户会直接看错的数：

- 一石只喂一条词条，五条**各自向上取整**后求和；先求和再取整会少给一颗。
- 差 0.1 也算喂满这一条，必须多一颗（向上取整，不是四舍五入）。
- 终点是配置里的**承音上限原值**，不是手算的 94%：131.2 占 139.6 的
  93.98%，按 94% 判会以为还能提升，实际已经到头。
- 当前值达到或超过承音上限时是 0 颗——承音那一刻会截断到上限。
- 目标等级取**承音之后**的等级，否则 110 未承音与 115 原生装备不可比。
- 每次提升比例来自游戏配置，不写死 4.5。
"""

from __future__ import annotations

import pytest

from lvjiang.apps.yysls.config.constants import DEFAULT_CHUANLV_STONE_PERCENT
from lvjiang.apps.yysls.config.manager import GameConfigManager
from lvjiang.apps.yysls.core.affix_cultivation import (
    estimate_equipment_stones,
    estimate_stones,
    stones_to_full,
)

_ATK = "最大外功攻击"

# 与系统配置一致的两个等级；单独建一份最小配置，免得用例跟着真配置漂移。
_CONFIG = """\
basic_config:
  chuanlv_stone_percent: 4.5
level_configs: []
affix_caps:
  外功攻击:
    _aliases:
    - 最大外功攻击
    110:
      cap: 121.4
      chengyin: 114.1
    115:
      cap: 139.6
      chengyin: 131.2
"""


@pytest.fixture
def game_config(tmp_path):
    path = tmp_path / "game_config.yaml"
    path.write_text(_CONFIG, encoding="utf-8")
    return GameConfigManager(path)


def _stones(game_config, value, level=115, percent=None):
    return estimate_stones(
        [(1, _ATK, value)], level, percent=percent, game_config=game_config,
    ).total


def test_stones_to_full_ceils_even_for_a_tiny_gap():
    """差 0.1 也算喂满这条词条：需要整整一颗，不能四舍五入成 0。"""
    assert stones_to_full(131.1, 131.2, 6.282) == 1


def test_stones_to_full_does_not_overshoot_on_exact_multiples():
    """正好三颗喂满时不能再多给一颗（浮点误差会让比值略大于 3）。"""
    value = 131.2 - 3 * 6.282
    assert stones_to_full(value, 131.2, 6.282) == 3


def test_stones_to_full_zero_when_already_at_or_above_target():
    assert stones_to_full(131.2, 131.2, 6.282) == 0
    assert stones_to_full(135.0, 131.2, 6.282) == 0


def test_five_affixes_sum_ceilings_not_ceiling_of_sum(game_config):
    """每条各差 1.5 颗时合计 4 颗（2+2），不是先求和再取整得到的 3 颗。"""
    value = 131.2 - 1.5 * 6.282
    estimate = estimate_stones(
        [(1, _ATK, value), (2, _ATK, value)], 115, game_config=game_config)
    assert [item.stones for item in estimate.items] == [2, 2]
    assert estimate.total == 4


def test_target_is_configured_chengyin_not_hand_computed_94_percent(game_config):
    """终点读配置原值：131.2 占 139.6 只有 93.98%，但已经不能再提升。"""
    assert 131.2 / 139.6 * 100 < 94.0
    assert _stones(game_config, 131.2) == 0
    assert _stones(game_config, 131.1) == 1


def test_custom_chengyin_cap_drives_the_target(game_config):
    """手工改过承音上限的词条按改后的值算，不按 94% 反推。"""
    tuned = _CONFIG.replace("chengyin: 131.2", "chengyin: 128.0")
    path = game_config._path
    path.write_text(tuned, encoding="utf-8")
    config = GameConfigManager(path)

    assert _stones(config, 127.0) == 1
    assert _stones(config, 128.0) == 0
    # 131.0 已超过改后的承音上限：承音会截断到 128，再喂也没有收益。
    # 按 94% 手算会得到 131.224，于是误判「还差 0.224」再喂一颗。
    assert _stones(config, 131.0) == 0


def test_target_level_is_post_chengyin_level(game_config):
    """110 未承音装备按 115 的满值/承音上限算，与 115 原生装备同口径。"""
    assert _stones(game_config, 100.0, level=115) == 5
    # 按 110 的承音上限 114.1、满值 121.4 会算出 3 颗，跨等级不可比
    assert _stones(game_config, 100.0, level=110) == 3


def test_percent_comes_from_config(game_config):
    """比例可配置：调大后同一件装备要的石头更少。"""
    assert game_config.get_chuanlv_stone_percent() == 4.5
    assert _stones(game_config, 100.0) == 5
    assert _stones(game_config, 100.0, percent=10.0) == 3


def test_missing_cap_is_reported_not_crashed(game_config):
    estimate = estimate_stones(
        [(1, "根本不存在的词条", 10.0), (2, _ATK, 100.0)], 115,
        game_config=game_config)
    assert estimate.total == 5
    assert estimate.unresolved == ("根本不存在的词条",)


def test_empty_affix_is_ignored(game_config):
    estimate = estimate_stones(
        [(1, "", 0.0), (2, _ATK, 100.0)], 115, game_config=game_config)
    assert len(estimate.items) == 1
    assert estimate.unresolved == ()


def test_equipment_snapshot_estimate(game_config):
    equip = {
        "level": 115,
        "affix_1": {"name": _ATK, "value": 120.0},
        "affix_2": {"name": _ATK, "value": 135.0},
        "affix_3": {"name": _ATK, "value": 131.2},
        "affix_4": {"name": "", "value": 0.0},
        "affix_5": {"name": _ATK, "value": 125.671},
    }
    estimate = estimate_equipment_stones(equip, game_config=game_config)
    # 2 + 0 + 0 + 0 + 1 = 3
    assert estimate.total == 3
    assert estimate.summary() == "当前装备承音喂满需要 3 个传律石（115 阶承音上限）"


def test_summary_reports_full_equipment(game_config):
    equip = {"level": 115, "affix_1": {"name": _ATK, "value": 131.2}}
    estimate = estimate_equipment_stones(equip, game_config=game_config)
    assert estimate.is_full
    assert estimate.summary() == "当前装备承音词条已满，无需传律石（115 阶承音上限）"


def test_system_config_exposes_the_default_percent():
    """系统配置必须带上传律石比例，否则估算退回代码缺省值。"""
    from pathlib import Path

    from lvjiang.apps.yysls.config import get_game_config

    path = (Path(__file__).resolve().parents[2]
            / "config/system/yysls/game_config/basic.yaml")
    percent = GameConfigManager(path).get_chuanlv_stone_percent()
    assert percent == DEFAULT_CHUANLV_STONE_PERCENT
    assert get_game_config().get_chuanlv_stone_percent() == pytest.approx(4.5)
