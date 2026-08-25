"""事件本地缓冲：两段式 NDJSON，容量上限，损坏文件容错。"""
from __future__ import annotations

import pytest

from lvjiang.core.telemetry import spool as spool_mod
from lvjiang.core.telemetry.schema import EventSchema, FieldSpec


@pytest.fixture(autouse=True)
def isolated_spool(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    yield


_SCHEMA = EventSchema(name="t", version=1, fields=(FieldSpec("x", str, choices=("a", "b")),))


def _event(v="a"):
    return _SCHEMA.validate({"x": v})


class TestAppendTypeCheck:
    def test_rejects_non_validated_event(self):
        with pytest.raises(TypeError):
            spool_mod.append({"x": "a"})  # type: ignore[arg-type]


class TestFlushAndTakeBatches:
    def test_empty_flush_is_noop(self):
        spool_mod.flush()
        assert spool_mod.take_batches(10) == []

    def test_append_then_flush_produces_readable_chunk(self):
        spool_mod.append(_event())
        spool_mod.append(_event("b"))
        spool_mod.flush()
        chunks = spool_mod.take_batches(10)
        assert len(chunks) == 1
        assert chunks[0].events == (
            {"schema": "t", "version": 1, "x": "a"},
            {"schema": "t", "version": 1, "x": "b"},
        )

    def test_auto_flush_at_threshold(self):
        for _ in range(spool_mod.FLUSH_EVERY):
            spool_mod.append(_event())
        # 达到阈值应已自动落盘，不需要显式 flush()
        chunks = spool_mod.take_batches(10)
        assert len(chunks) == 1
        assert len(chunks[0].events) == spool_mod.FLUSH_EVERY

    def test_drop_removes_file(self):
        spool_mod.append(_event())
        spool_mod.flush()
        chunk = spool_mod.take_batches(10)[0]
        spool_mod.drop(chunk)
        assert spool_mod.take_batches(10) == []

    def test_drop_missing_file_does_not_raise(self):
        spool_mod.append(_event())
        spool_mod.flush()
        chunk = spool_mod.take_batches(10)[0]
        spool_mod.drop(chunk)
        spool_mod.drop(chunk)  # 已删除，再删一次不应报错

    def test_max_batches_limit(self):
        for _ in range(3):
            spool_mod.append(_event())
            spool_mod.flush()
        assert len(spool_mod.take_batches(2)) == 2
        assert len(spool_mod.take_batches(10)) == 3

    def test_oldest_batch_returned_first(self):
        import time
        for label in ("a", "b", "a"):
            spool_mod.append(_event(label))
            spool_mod.flush()
            time.sleep(0.01)
        chunks = spool_mod.take_batches(10)
        assert [c.path.stat().st_mtime for c in chunks] == sorted(
            c.path.stat().st_mtime for c in chunks)


class TestReadyFileContentEqualsWireContent:
    """磁盘上 ready/*.ndjson 逐字节等于将要发出的内容——用户点「查看待
    上报数据」看到的就是真相，不存在发送时再补字段的暗门。"""

    def test_persisted_bytes_match_parsed_events(self):
        spool_mod.append(_event("a"))
        spool_mod.flush()
        chunk = spool_mod.take_batches(10)[0]
        import json
        lines = [json.loads(ln) for ln in chunk.path.read_text(encoding="utf-8").splitlines() if ln]
        assert lines == list(chunk.events)


class TestCorruptedTail:
    def test_trailing_garbage_line_is_dropped_silently(self):
        spool_mod.append(_event())
        spool_mod.flush()
        chunk = spool_mod.take_batches(10)[0]
        with open(chunk.path, "a", encoding="utf-8") as f:
            f.write("not valid json\n")
        chunks = spool_mod.take_batches(10)
        assert len(chunks[0].events) == 1  # 只有那条合法行，损坏尾行静默丢弃


class TestCapacityLimits:
    def test_exceeding_max_ready_files_drops_oldest(self, monkeypatch):
        monkeypatch.setattr(spool_mod, "MAX_READY_FILES", 2)
        for _ in range(4):
            spool_mod.append(_event())
            spool_mod.flush()
        chunks = spool_mod.take_batches(10)
        assert len(chunks) == 2

    def test_dropped_events_bumped_into_identity(self, monkeypatch):
        from lvjiang.core.telemetry import identity as identity_mod
        identity_mod.get_identity()
        monkeypatch.setattr(spool_mod, "MAX_READY_FILES", 1)
        for _ in range(3):
            spool_mod.append(_event())
            spool_mod.flush()
        assert identity_mod.take_and_reset_dropped_events() > 0


class TestPurge:
    def test_purge_clears_everything(self):
        spool_mod.append(_event())
        spool_mod.flush()
        assert spool_mod.take_batches(10)
        spool_mod.purge()
        assert spool_mod.take_batches(10) == []

    def test_purge_clears_in_memory_buffer_too(self):
        spool_mod.append(_event())  # 未 flush，还在内存里
        spool_mod.purge()
        spool_mod.flush()
        assert spool_mod.take_batches(10) == []


class TestPendingEventCount:
    def test_counts_only_flushed_events(self):
        spool_mod.append(_event())
        assert spool_mod.pending_event_count() == 0  # 未 flush
        spool_mod.flush()
        assert spool_mod.pending_event_count() == 1
