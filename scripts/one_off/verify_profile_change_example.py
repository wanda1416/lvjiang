"""隔离验证 local/workflows/profile/example.wf；不读取或修改真实 Profile 数据。

运行：python scripts/one_off/verify_profile_change_example.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from lvjiang.core.paths import get_config_local_dir  # noqa: E402
from lvjiang.core.profile import engine, repository, schema, triggers  # noqa: E402
from lvjiang.core.profile.service import (  # noqa: E402
    delete_profile_history,
    profile_action,
    profile_read,
)
from lvjiang.core.profile.transactions import ProfileWriteRejected  # noqa: E402
from lvjiang.workflows.grammar import parse_file  # noqa: E402


def main() -> None:
    if len(sys.argv) == 1:
        wf = get_config_local_dir() / 'workflows/profile/example.wf'
        with tempfile.TemporaryDirectory(prefix='lvjiang-example-root-') as isolated_root:
            environment = dict(os.environ, LVJIANG_ROOT=isolated_root)
            business = get_config_local_dir() / 'workflows/profile/chengyinshi_limit.wf'
            subprocess.run([sys.executable, str(Path(__file__).resolve()), str(wf), str(business)],
                           env=environment, check=True, text=True, encoding='utf-8')
        return
    # 整个子进程（包括运行快照/content.json）均在临时根下，不能只隔离 DB。
    wf = Path(sys.argv[1])
    logger.remove()
    logger.add(sys.stderr, level='WARNING')
    parse_file(wf)
    # 示例注释中的 YAML 就是唯一的配套定义，避免复制另一份 key/模式清单。
    lines = wf.read_text(encoding='utf-8').splitlines()
    start = lines.index('# stock:')
    end = lines.index('#', start)
    definitions = yaml.safe_load('\n'.join(line[2:] for line in lines[start:end]))
    with tempfile.TemporaryDirectory(prefix='lvjiang-profile-example-') as folder:
        root = Path(folder)
        cfg = root / 'profile.yaml'
        cfg.write_text(yaml.safe_dump(definitions, allow_unicode=True), encoding='utf-8')
        runner = triggers.ProfileScriptRunner(root / 'users')
        events = []
        failures = []
        original = runner._run_event
        def run(event):
            events.append((event, threading.get_ident()))
            try:
                original(event)
            except Exception:
                failures.append(event.key)
                raise
        runner._run_event = run
        with patch.object(schema, '_PROFILE_PATH', cfg), patch.object(schema, '_config', None), \
                patch.object(repository, '_DB_PATH', root / 'profile.db'), \
                patch.object(triggers, '_runner', runner), \
                patch('lvjiang.workflows.discovery.resolve_workflow_path', return_value=(wf, 'profile/example.wf')):
            repository.reset_profile_db()
            try:
                def drain():
                    deadline = time.monotonic() + 5
                    while runner.is_busy and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert not runner.is_busy, '示例触发链未结束'

                user = 'example-user'
                profile_action(user, 'example_source', delta=1)
                drain()
                assert profile_read(user, 'example_sync_result') == 2
                assert profile_read(user, 'example_async_result') == 1
                assert any(e.key == 'example_sync' and e.origin_key == 'example_source' for e, _ in events)
                source = next(e for e, _ in events if e.key == 'example_source')
                caller = threading.get_ident()
                assert next(tid for e, tid in events if e is source) == caller
                assert next(tid for e, tid in events if e.key == 'example_async') != caller

                count = len(events)
                profile_action(user, 'example_source', set_value=1)
                assert len(events) == count, '同值不应触发'
                profile_action(user, 'example_source', delta=-1)
                profile_action(user, 'example_source', set_value=5, is_action=False)
                drain()
                assert any(e.change_type == 'override' for e, _ in events)
                assert any(e.delta == -1 for e, _ in events)

                profile_action(user, 'example_capped', set_value=8)
                profile_action(user, 'example_capped', delta=5)
                assert events[-1][0].delta == 2 and events[-1][0].new_value == 10
                profile_action(user, 'example_note', set_value='example text')
                assert profile_read(user, 'example_text_result') == 'example text'
                count = len(events)
                profile_action(user, 'example_note', set_value='example text')
                assert len(events) == count

                old = (datetime.now() - timedelta(days=2)).isoformat()
                repository.db_upsert(user, 'quota', 'example_period', 0, updated_at=old)
                repository.db_upsert(user, 'regen', 'example_regen', 0, updated_at=old)
                schema.get_profile_config().get_key('example_regen').regen_amount = 2
                worker = SimpleNamespace(data_updated=SimpleNamespace(emit=lambda _: None))
                engine.ProfileEngine._tick_user(worker, user, schema.get_profile_config())
                assert any(e.change_type == 'reset' and e.old_value == e.new_value == 0 for e, _ in events)
                assert any(e.change_type == 'tick' for e, _ in events)

                profile_action(user, 'example_inverse', set_value=5)
                before = profile_read(user, 'example_sync')
                profile_action(user, 'example_declared', delta=2)
                assert profile_read(user, 'example_sync') == before + 4
                assert profile_read(user, 'example_fraction') == 1
                assert profile_read(user, 'example_inverse') == 3
                profile_action(user, 'example_declared', delta=-1)
                assert profile_read(user, 'example_fraction') == 1  # pos 方向不处理减少。
                assert profile_read(user, 'example_inverse') == 4
                before = profile_read(user, 'example_sync')
                profile_action(user, 'example_declared', set_value=9, is_action=False)
                assert profile_read(user, 'example_sync') == before  # override 不触发声明式同步。

                try:
                    profile_action(user, 'example_direct_cycle', delta=1)
                except ProfileWriteRejected:
                    pass
                else:
                    raise AssertionError('直接循环应拒绝整链')
                assert profile_read(user, 'example_direct_cycle') is None
                profile_action(user, 'example_cycle_a', delta=1)
                drain()
                assert profile_read(user, 'example_cycle_a') == profile_read(user, 'example_cycle_b') == 1
                try:
                    profile_action(user, 'example_failure', delta=1)
                except ValueError:
                    pass
                else:
                    raise AssertionError('同步异常应拒绝整链')
                assert profile_read(user, 'example_failure') is None
                profile_action(user, 'example_note', set_value='after failure')
                assert profile_read(user, 'example_text_result') == 'after failure'
                assert failures == ['example_direct_cycle', 'example_cycle_b', 'example_failure'], failures
                print('PASS: 同步/异步/混合链、事件快照、同值、钳位、文本、reset/tick、声明式同步、防环、失败隔离')
                verify_source_limits(Path(sys.argv[2]), runner)
            finally:
                runner.request_stop()
                if runner.is_alive():
                    runner.join(3)
                repository.reset_profile_db()


def verify_source_limits(wf: Path, runner: triggers.ProfileScriptRunner) -> None:
    """真实执行来源限制脚本；参数仅从脚本读取，不复制另一份上限清单。"""
    from lvjiang.core.profile.models import StockKeyDef

    parameters = next(line for line in wf.read_text(encoding='utf-8').splitlines()
                      if line.startswith('$source_limits = '))
    limits = json.loads(parameters.split('=', 1)[1])
    config = schema.get_profile_config()
    key = 'peiyang_chengyinshi'
    config.keys_by_model['stock'].append(StockKeyDef(
        key=key, change_script='profile/chengyinshi_limit.wf', change_script_sync=True))
    config._rebuild_index()
    with patch('lvjiang.workflows.discovery.resolve_workflow_path', return_value=(wf, 'profile/chengyinshi_limit.wf')):
        def denied(user, source, *, delta=1, set_value=None, is_action=True):
            before = repository.db_read_entry(user, 'stock', key)
            count = repository.db_count_history(user, 'stock', key)
            try:
                profile_action(user, key, delta=delta, source=source, set_value=set_value, is_action=is_action)
            except ProfileWriteRejected:
                pass
            else:
                raise AssertionError(f'应拒绝获取: {source}')
            assert repository.db_read_entry(user, 'stock', key) == before
            assert repository.db_count_history(user, 'stock', key) == count

        for index, (source, cap) in enumerate(limits.items()):
            user = f'example-limit-{index}'
            profile_action(user, key, delta=cap - 1, source=source)
            denied(user, source, delta=2)  # 跨越上限也必须拒绝，不能只比较旧累计。
            profile_action(user, key, delta=1, source=source)
            assert profile_read(user, key) == cap
            denied(user, source)
            profile_action(user, key, delta=-1, source='example consumption')
            denied(user, source)  # 消耗不能释放获取额度。
            denied(user, source, set_value=cap, is_action=False)  # 正向覆写不能绕过。
            receipt = next(row for row in repository.db_get_history(user, 'stock', key)
                           if row['delta_value'] == 1)
            delete_profile_history(receipt['id'], user, 'stock', key)
            assert profile_read(user, key) == cap - 1
            profile_action(user, key, delta=1, source=source)  # 删除历史释放额度，但不改余额。
        denied('example-invalid-source', 'unknown source')
        assert not runner.is_busy
    print('PASS: 五个来源上限、跨界拒绝、未知来源拒绝、消耗不退额度、覆写限制、删除历史释放额度')


if __name__ == '__main__':
    main()
