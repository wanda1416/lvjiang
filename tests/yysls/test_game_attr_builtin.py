"""game_attr 内置函数测试

工作流拿它替代硬编码的赛季名，所以三件事必须成立：读的是游戏配置里的当前赛季、
没有生效赛季时给空值而不是抛错、属性名写错时也不会把工作流带崩。
"""

import pytest

# 导入以注册内置函数
import lvjiang.apps.yysls.workflows.builtins.game_attr  # noqa: F401
from lvjiang.apps.yysls import config as yysls_config
from lvjiang.workflows.builtins import get_function


@pytest.fixture
def game_attr():
    fn = get_function("game_attr")
    assert fn is not None, "内置函数 game_attr 未注册"
    return fn


class _FakeSeason:
    def __init__(self, name):
        self.name = name


class _FakeConfig:
    def __init__(self, season):
        self._season = season

    def current_season(self):
        return self._season


@pytest.fixture
def fake_season(monkeypatch):
    def _apply(season):
        monkeypatch.setattr(
            yysls_config, "get_game_config", lambda: _FakeConfig(season))
    return _apply


def test_season_name_reads_current_season(game_attr, fake_season):
    fake_season(_FakeSeason("南吕相和"))
    assert game_attr("season_name") == "南吕相和"


def test_season_name_empty_when_no_active_season(game_attr, fake_season):
    """赛季配置过期时给空值，让脚本自己降级判据，而不是炸在半路。"""
    fake_season(None)
    assert game_attr("season_name") == ""


def test_unknown_attr_returns_empty(game_attr, fake_season):
    fake_season(_FakeSeason("南吕相和"))
    assert game_attr("no_such_attr") == ""
