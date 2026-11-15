"""Profile 写入事务的提交边界。

``profile_transaction`` 让一次同步链里的多次读写共用一条连接、一次提交，并把
通知压到提交之后。仓储从单模块拆成 entries/ledger/sync_log 之后，这条边界最
容易被拆坏：写入各自 ``BEGIN/COMMIT`` 会让半成品对其他进程可见，失败时提前派发
通知会让界面刷新出并不存在的数据。

两个用例分别保护：
- 提交之前外部连接读不到本次写入，``after_commit`` 回调也不执行；提交后一次可见；
- 事务回滚时回调被丢弃，条目的值与账本行都不留痕。
"""

from __future__ import annotations

import pytest

from lvjiang.core.profile.repository import ProfileDB
from lvjiang.core.profile.transactions import after_commit, profile_transaction


def test_after_commit_callbacks_run_only_after_the_write_is_visible(tmp_path):
    db = ProfileDB(tmp_path / "profile.db")
    observer = ProfileDB(tmp_path / "profile.db")
    fired: list[float] = []

    with profile_transaction(db):
        db.upsert("tester", "stock", "item", 5, op_type="set", source="测试")
        after_commit(
            lambda: fired.append(observer.get_entry("tester", "stock", "item").value))
        assert fired == []
        # 事务未提交时，另一条连接看不到这次写入：整条链只提交一次。
        assert observer.get_entry("tester", "stock", "item") is None

    assert fired == [5.0]
    assert db.get_history("tester", "stock", "item")[0].op_type == "set"


def test_after_commit_callbacks_are_dropped_when_the_transaction_rolls_back(tmp_path):
    db = ProfileDB(tmp_path / "profile.db")
    fired: list[bool] = []

    with pytest.raises(RuntimeError, match="回滚"):
        with profile_transaction(db):
            db.upsert("tester", "stock", "item", 1, op_type="set")
            after_commit(lambda: fired.append(True))
            raise RuntimeError("回滚")

    assert fired == []
    assert db.get_entry("tester", "stock", "item") is None
    assert db.get_history("tester", "stock", "item") == []
