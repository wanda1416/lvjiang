"""逐战商店配额：依赖 yysls 注册的赛季周期（half_season）与通用 Profile 注册表。

注册表本身的契约见 ``tests/workflows/test_profile_registry.py``。
"""
from importlib import import_module

from tests.workflows.test_profile_registry import (  # noqa: F401 - 共用夹具与工具
    _declare,
    isolated_profile_config,
)


def test_zhuzhan_quotas_keep_distinct_periods_and_currency_costs(isolated_profile_config):  # noqa: F811
    from lvjiang.core.profile.repository import db_get_history, get_profile_db
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.core.profile.service import profile_action, profile_read

    import_module("lvjiang.apps.yysls.core.season_periods")
    assert _declare(["huaruizhi", "niaoniao", "nn_of_halfseason", "zhuzhanbox_of_halfseason"]) == 0
    config = get_profile_config()
    nn = config.get_key("nn_of_halfseason")
    boxes = config.get_key("zhuzhanbox_of_halfseason")
    assert (nn.cap, nn.period) == (2, "half_season")
    assert (boxes.cap, boxes.period, boxes.reset_day) == (5, "week", 1)
    assert nn.sync_targets[0].key == boxes.sync_targets[0].key == "stock:huaruizhi"
    assert nn.sync_targets[0].ratio == -3000
    assert boxes.sync_targets[0].ratio == -200

    db = get_profile_db()
    db.upsert("tester", "stock", "huaruizhi", 7000)
    db.upsert("tester", "stock", "niaoniao", 2)
    profile_action("tester", "zhuzhanbox_of_halfseason", delta=5, source="逐战商店")
    profile_action("tester", "nn_of_halfseason", delta=2, source="逐战商店")
    assert profile_read("tester", "huaruizhi") == 0
    assert profile_read("tester", "niaoniao") == 4
    spending = db_get_history("tester", "stock", "huaruizhi")
    assert [row.source for row in spending] == ["袅袅之音", "逐战礼盒"]
    incoming = db_get_history("tester", "stock", "niaoniao")
    assert incoming[0].source == "逐战商店"
