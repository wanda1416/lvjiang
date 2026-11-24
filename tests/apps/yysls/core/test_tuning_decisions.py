"""自动调律行为值对象测试。"""

import pytest

from lvjiang.apps.yysls.workflows.implementations.tuning.decisions import (
    BehaviorAction,
    BehaviorDecision,
)


def test_behavior_decision_normalizes_known_action():
    decision = BehaviorDecision.from_raw("recycle", "命中回收规则")

    assert decision.action is BehaviorAction.RECYCLE
    assert str(decision.action) == "recycle"
    assert decision.reason == "命中回收规则"


def test_behavior_decision_rejects_unknown_action():
    with pytest.raises(ValueError, match="未知自动调律行为"):
        BehaviorDecision.from_raw("silently_keep", "bad config")
