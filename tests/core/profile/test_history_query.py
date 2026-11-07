"""变更记录的筛选 / 聚合 / 分页契约。

这些用例保护的是"结果本身对不对"，不是实现细节：

- 聚合候选必须按**其它**条件统计——勾选一个来源之后，候选列表不能塌缩成
  只剩它自己，否则想再加一个来源只能靠记忆输入。
- 空来源是真实取值，必须能单独筛出来（库里 27% 的历史没有来源），不能和
  "不限"混成一种状态。
- 用户输入的 ``%``/``_`` 是普通字符，不能当成 LIKE 通配符。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from lvjiang.core.profile.history_query import HistoryQuery
from lvjiang.core.profile.repository import ProfileDB


@pytest.fixture
def db(tmp_path: Path) -> ProfileDB:
    db = ProfileDB(tmp_path / "history_query.db")
    db.upsert("甲", "stock", "jinbi", 5, updated_at="2026-10-01T08:00:00",
              change_type="set", source="日常")
    db.upsert("甲", "stock", "jinbi", 8, updated_at="2026-10-02T08:00:00",
              change_type="delta", delta_value=3, source="",
              sync_from="quota:weekly")
    db.upsert("甲", "quota", "weekly", 2, updated_at="2026-10-03T08:00:00",
              change_type="delta", delta_value=2, source="限时活动：商城")
    db.upsert("乙", "quota", "weekly", 1, updated_at="2026-10-04T08:00:00",
              change_type="set", source="日常")
    db.upsert("乙", "quota", "weekly", 0, updated_at="2026-10-05T08:00:00",
              change_type="reset", delta_value=-1, source="周期重置")
    return db


def _facet(db: ProfileDB, query: HistoryQuery, dimension: str) -> dict[str, int]:
    return dict(db.history_facets(query, dimension, limit=10))


class TestFacets:

    def test_candidates_ignore_their_own_condition(self, db: ProfileDB):
        """选了来源之后，来源候选仍然是全部来源（Excel 语义）。"""
        query = HistoryQuery(sources=("日常",))
        candidates = _facet(db, query, "source")
        assert set(candidates) == {"日常", "", "限时活动：商城", "周期重置"}

    def test_candidates_follow_other_conditions(self, db: ProfileDB):
        """别的维度照常收窄候选：只看"甲"时，来源里没有"周期重置"。"""
        query = HistoryQuery(usernames=("甲",))
        candidates = _facet(db, query, "source")
        assert set(candidates) == {"日常", "", "限时活动：商城"}

    def test_candidates_can_be_searched(self, db: ProfileDB):
        rows = db.history_facets(HistoryQuery(), "source", limit=10, search="商城")
        assert [row[0] for row in rows] == ["限时活动：商城"]
        assert db.history_facet_total(HistoryQuery(), "source", search="商城") == 1

    def test_key_candidates_are_model_and_key_pairs(self, db: ProfileDB):
        candidates = _facet(db, HistoryQuery(), "key")
        assert candidates == {"stock:jinbi": 2, "quota:weekly": 3}


class TestFilters:

    def test_empty_source_is_its_own_value(self, db: ProfileDB):
        assert db.history_count(HistoryQuery(sources=("",))) == 1
        assert db.history_count(HistoryQuery(sources=("日常",))) == 2
        assert db.history_count(HistoryQuery()) == 5

    def test_like_wildcards_are_literal(self, db: ProfileDB):
        assert db.history_count(HistoryQuery(keyword="%")) == 0
        assert db.history_count(HistoryQuery(keyword="商城")) == 1

    def test_key_label_matches_combine_with_keyword(self, db: ProfileDB):
        """关键词命中键标签时按 (模型, 键) 命中，与跨列模糊是"或"关系。"""
        query = HistoryQuery(
            keyword="商城", key_matches=(("quota", "weekly"),))
        assert db.history_count(query) == 3

    def test_delta_scope_and_time_window(self, db: ProfileDB):
        assert db.history_count(HistoryQuery(delta_scope="pos")) == 2
        assert db.history_count(HistoryQuery(delta_scope="neg")) == 1
        assert db.history_count(HistoryQuery(delta_scope="zero")) == 0
        # 历史行的 ts 由写入时刻决定，时间窗口按"今天"上下界验证。
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert db.history_count(
            HistoryQuery(ts_from=yesterday, ts_until=tomorrow)) == 5
        assert db.history_count(HistoryQuery(ts_from=tomorrow)) == 0

    def test_value_text_filter_hits_numbers_too(self, db: ProfileDB):
        assert db.history_count(HistoryQuery(new_text="8")) == 1
        assert db.history_count(HistoryQuery(old_text="1")) == 1

    def test_delta_totals_follow_filters(self, db: ProfileDB):
        gained, spent = db.history_delta_totals(HistoryQuery())
        assert round(gained, 6) == 5.0
        assert round(spent, 6) == -1.0


class TestPaging:

    def test_newest_first_and_offset(self, db: ProfileDB):
        first = db.history_page(HistoryQuery(), limit=2)
        second = db.history_page(HistoryQuery(), limit=2, offset=2)
        assert [row["id"] for row in first] > [row["id"] for row in second]
        assert len(second) == 2
        assert len(db.history_page(HistoryQuery(), limit=10, offset=4)) == 1

    def test_order_is_whitelisted(self, db: ProfileDB):
        """非法排序名退回默认倒序，不把它拼进 SQL。"""
        rows = db.history_page(HistoryQuery(), limit=1, order="id; DROP TABLE x")
        assert len(rows) == 1


class TestQueryObject:

    def test_relaxed_drops_only_that_dimension(self):
        query = HistoryQuery(
            usernames=("甲",), keys=(("quota", "weekly"),),
            change_types=("set",), sources=("日常",),
        )
        relaxed = query.relaxed("change_type")
        assert relaxed.change_types == ()
        assert relaxed.usernames == ("甲",)
        assert relaxed.sources == ("日常",)
        # 「键」维度包含模型类型与键名，放松时一起清掉
        assert query.relaxed("key").keys == ()
        assert query.relaxed("key").model_types == ()

    def test_unknown_inputs_are_rejected(self):
        with pytest.raises(ValueError):
            HistoryQuery(delta_scope="sideways")
        with pytest.raises(ValueError):
            HistoryQuery().relaxed("nope")

    def test_empty_query_means_everything(self):
        assert HistoryQuery().is_empty
        assert not HistoryQuery(usernames=("甲",)).is_empty
        assert not HistoryQuery(sources=("",)).is_empty
