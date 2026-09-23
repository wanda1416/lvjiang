"""扫描全部备战方案时的玩法匹配。

同一流派同一武学组合下可以并存多个玩法（牵丝·霖的火拳和纯奶武学完全相同），
而方案名未必写着玩法名。只靠「名字里含玩法名」会落空并静默取第一个候选——
用户看到的是匹配错了，日志里却什么异常都没有。
"""

import pytest

from lvjiang.apps.yysls.workflows.builtins.equipment_ingest import (
    _match_playstyle,
)

_STYLES = {
    "火拳": {"match_keywords": ["输出"]},
    "纯奶": {"match_keywords": ["奶"]},
    "无关": {"match_keywords": []},
}
_CANDIDATES = ["火拳", "纯奶"]


def _match(name: str, candidates: list[str] | None = None) -> str:
    return _match_playstyle(
        name, _CANDIDATES if candidates is None else candidates, _STYLES)


def test_plan_name_containing_the_playstyle_wins_over_keywords():
    """方案名里直接写了玩法名，就不该被别的玩法的关键字抢走。

    「火拳输出」同时含玩法名「火拳」和火拳自己的关键字；但换成一个含别家
    关键字的名字时，玩法名仍必须优先——它是最具体的信号。
    """
    assert _match("纯奶输出") == "纯奶"
    assert _match("我的火拳") == "火拳"


def test_longest_keyword_wins_when_several_hit():
    """「输出奶」同时命中火拳的「输出」和纯奶的「奶」，取长的才选中火拳。

    按配置声明顺序取第一个命中的话，结果取决于玩法在 yaml 里谁在前，
    和用户的意图无关。
    """
    assert _match("输出奶") == "火拳"


def test_keyword_only_matches_when_contained_in_the_name():
    assert _match("奶妈") == "纯奶"
    assert _match("随便起的名字") == "火拳", "都落空时回落第一个候选"


def test_no_candidate_yields_empty_playstyle():
    assert _match("输出奶", []) == ""


@pytest.mark.parametrize("keywords", [None, [], "", ["  "]])
def test_missing_or_blank_keywords_never_crash(keywords):
    """配置里没填、填空串、填一串空格都要当成没有关键字。"""
    styles = {"火拳": {"match_keywords": keywords}}
    assert _match_playstyle("输出奶", ["火拳"], styles) == "火拳"


def test_config_normalizes_keywords_into_a_clean_list():
    """归一化沿用 _string_list：去空白、去空值、去重、保序。"""
    from lvjiang.apps.yysls.config.manager import _string_list

    assert _string_list([" 输出 ", "", "输出", "奶"]) == ["输出", "奶"]
