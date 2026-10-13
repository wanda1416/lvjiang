"""历史聚合不可跨用户串数，也不能用消耗抵扣累计获取量。"""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from lvjiang.core.profile.repository import ProfileDB, get_profile_db
from lvjiang.core.profile.transactions import profile_transaction
from lvjiang.workflows.builtins.profile import _profile_history_aggregate


def test_aggregate_filters_user_source_type_direction_and_time():
    db = get_profile_db()
    # 有一条未声明 delta 的历史，聚合应使用已保存的新旧值而不是漏掉。
    db.upsert('tester', 'stock', 'item', 2, change_type='action', source='supply')
    db.upsert('tester', 'stock', 'item', 1, change_type='action', delta_value=-1, source='supply')
    db.upsert('tester', 'stock', 'item', 4, change_type='override', delta_value=3, source='supply')
    db.upsert('tester', 'stock', 'item', 5, change_type='action', delta_value=1, source='other')
    db.upsert('other-user', 'stock', 'item', 99, change_type='action', delta_value=99, source='supply')
    env = SimpleNamespace(run_username='tester')
    assert _profile_history_aggregate(env, 'stock:item', 'sum', 'supply', ['action', 'override'], 'pos') == 5
    assert _profile_history_aggregate(env, 'stock:item', 'sum', 'supply', ['action'], 'all') == 1
    assert _profile_history_aggregate(env, 'stock:item', 'count', 'supply', ['action'], 'neg') == 1
    assert _profile_history_aggregate(env, 'stock:item', 'max', 'supply', None, 'pos') == 3
    assert _profile_history_aggregate(env, 'stock:item', 'min', 'supply', None, 'pos') == 2
    assert _profile_history_aggregate(env, 'stock:item', 'avg', 'supply', None, 'pos') == 2.5
    timestamp = db.get_history('tester', 'stock', 'item')[0]['ts']
    assert _profile_history_aggregate(env, 'stock:item', 'sum', 'supply', None, 'pos', timestamp, timestamp) == 0
    assert _profile_history_aggregate(env, 'stock:item', 'sum', 'missing') == 0
    with pytest.raises(ValueError):
        _profile_history_aggregate(env, 'stock:item', 'unsafe SQL')


def test_aggregate_sees_transaction_writes_but_rollback_discards_history():
    db = get_profile_db()
    with pytest.raises(RuntimeError, match='rollback'):
        with profile_transaction(db):
            db.upsert('tester', 'stock', 'item', 2, change_type='action', delta_value=2, source='supply')
            assert db.get_entry('tester', 'stock', 'item')['value'] == 2
            assert db.aggregate_history('tester', 'stock', 'item', source='supply') == 2
            raise RuntimeError('rollback')
    assert db.aggregate_history('tester', 'stock', 'item', source='supply') == 0
    assert not db.get_entry('tester', 'stock', 'item')


def test_independent_connections_cannot_both_read_and_spend_remaining_limit(tmp_path):
    first = ProfileDB(tmp_path / 'shared.db')
    second = ProfileDB(tmp_path / 'shared.db')
    staged, release, attempting = threading.Event(), threading.Event(), threading.Event()
    def receive_first():
        with profile_transaction(first):
            first.upsert('tester', 'stock', 'item', 1, change_type='action', delta_value=1, source='supply')
            staged.set()
            assert release.wait(3)
    def receive_second():
        attempting.set()
        with profile_transaction(second):
            received = second.aggregate_history('tester', 'stock', 'item', source='supply', direction='pos')
            if received >= 1:
                return False
            second.upsert('tester', 'stock', 'item', 1, change_type='action', delta_value=1, source='supply')
            return True
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(receive_first)
        try:
            assert staged.wait(3)
            two = pool.submit(receive_second)
            assert attempting.wait(3)
        finally:
            release.set()
        one.result(timeout=3)
        assert two.result(timeout=3) is False
    assert second.aggregate_history('tester', 'stock', 'item', source='supply', direction='pos') == 1
