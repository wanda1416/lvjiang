"""note 模型综合测试

覆盖 note 模型的全链路：
- NoteKeyDef 序列化/反序列化
- profile.yaml 含 note 节点正确加载
- DB value_text 列读写
- profile_action() note 短路写入
- profile_read() note 返回文本
- ProfileEngine tick 不处理 note
- note sync_targets 不触发
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture
def note_env(tmp_path, monkeypatch):
    """隔离的 note 测试环境"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.apps.yysls.config.user_profile as profile_config
    import lvjiang.apps.yysls.core.profile_engine.profile_db as profile_db

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "note": [
                    {"key": "took_xinfa", "label": "是否拿心法"},
                    {
                        "key": "team_note",
                        "label": "队伍备注",
                        "description": "记录队伍配置思路",
                    },
                ],
                "stock": [
                    {"key": "target_stock", "label": "同步目标"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="test_user", session_dir=session_dir)

    profile_config._config = None
    profile_db._db = None


# ─── NoteKeyDef 序列化 ────────────────────────────────────────


class TestNoteKeyDef:
    def test_from_dict(self):
        from lvjiang.apps.yysls.config.profile_models import NoteKeyDef

        kd = NoteKeyDef.from_dict({
            "key": "took_xinfa",
            "label": "是否拿心法",
            "description": "记录心法获取状态",
        })
        assert kd.key == "took_xinfa"
        assert kd.label == "是否拿心法"
        assert kd.description == "记录心法获取状态"
        assert isinstance(kd, NoteKeyDef)

    def test_from_dict_defaults(self):
        from lvjiang.apps.yysls.config.profile_models import NoteKeyDef

        kd = NoteKeyDef.from_dict({"key": "k", "label": "l"})
        assert kd.sources == []
        assert kd.uses == []
        assert kd.sync_targets == []
        assert kd.cap is None

    def test_to_dict(self):
        from lvjiang.apps.yysls.config.profile_models import NoteKeyDef

        kd = NoteKeyDef(key="k", label="l", description="desc")
        d = kd.to_dict()
        assert d["key"] == "k"
        assert d["label"] == "l"
        assert d["description"] == "desc"

    def test_roundtrip(self):
        from lvjiang.apps.yysls.config.profile_models import NoteKeyDef

        original = NoteKeyDef(
            key="note1", label="备注1",
            description="测试描述",
            sources=["来源1"],
            uses=["用途1"],
        )
        d = original.to_dict()
        restored = NoteKeyDef.from_dict(d)
        assert restored.key == original.key
        assert restored.label == original.label
        assert restored.description == original.description
        assert restored.sources == original.sources
        assert restored.uses == original.uses


# ─── 配置加载 ──────────────────────────────────────────────────


class TestNoteConfigLoading:
    def test_load_note_from_yaml(self, note_env):
        """profile.yaml 含 note 节点正确加载"""
        from lvjiang.apps.yysls.config.user_profile import _load_config
        from lvjiang.apps.yysls.config.profile_models import NoteKeyDef

        schema = _load_config()
        kd = schema.get_key("took_xinfa")
        assert kd is not None
        assert isinstance(kd, NoteKeyDef)
        assert kd.label == "是否拿心法"

    def test_get_model_type_note(self, note_env):
        """note key 的 model_type 为 'note'"""
        from lvjiang.apps.yysls.config.user_profile import _load_config

        schema = _load_config()
        assert schema.get_model_type("took_xinfa") == "note"

    def test_get_keys_by_model_note(self, note_env):
        """get_keys_by_model('note') 返回所有 note key"""
        from lvjiang.apps.yysls.config.user_profile import _load_config

        schema = _load_config()
        note_keys = schema.get_keys_by_model("note")
        assert len(note_keys) == 2
        key_names = {kd.key for kd in note_keys}
        assert key_names == {"took_xinfa", "team_note"}


# ─── DB value_text 读写 ────────────────────────────────────────


class TestNoteDB:
    def test_upsert_and_read_value_text(self, note_env):
        """DB value_text 列读写正常"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "已拿"
        assert entry["value"] == 0

    def test_value_text_empty_by_default(self, note_env):
        """不传 value_text 时默认空字符串"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "took_xinfa", 0)
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == ""

    def test_value_text_overwrite(self, note_env):
        """value_text 可被覆写"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")
        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="未拿")
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "未拿"


# ─── profile_action note 短路 ──────────────────────────────────


class TestNoteProfileAction:
    def test_note_short_circuit_write(self, note_env):
        """profile_action() note 文本写入走 db_upsert(value_text=...)"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

        result = profile_action(
            note_env.username, "took_xinfa",
            model_type="note",
            set_value="已拿",
            source="test",
        )
        assert result == "已拿"
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "已拿"

    def test_note_short_circuit_empty_text(self, note_env):
        """note 写入空文本"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

        result = profile_action(
            note_env.username, "took_xinfa",
            model_type="note",
            set_value="",
        )
        assert result == ""
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == ""

    def test_note_no_history(self, note_env):
        """note 写入不记入 history"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import ProfileDB
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

        profile_action(
            note_env.username, "took_xinfa",
            model_type="note",
            set_value="已拿",
        )
        # 直接查 DB history 表
        import lvjiang.apps.yysls.core.profile_engine.profile_db as pdb
        db: ProfileDB = pdb._db
        history = db.get_history(note_env.username)
        assert history == []

    def test_note_no_sync_targets(self, note_env):
        """note 不触发 sync_targets"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_action

        # 先给同步目标设初始值
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_upsert
        db_upsert(note_env.username, "stock", "target_stock", 1000)

        # note 写入（即使 note key 定义了 sync_targets，也不会触发）
        profile_action(
            note_env.username, "took_xinfa",
            model_type="note",
            set_value="已拿",
        )

        # 同步目标值不变
        target = db_read_entry(note_env.username, "stock", "target_stock")
        assert target["value"] == 1000


# ─── profile_read note 返回文本 ────────────────────────────────


class TestNoteProfileRead:
    def test_profile_read_note_returns_text(self, note_env):
        """profile_read() note 返回文本"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_upsert
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_read

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")
        result = profile_read(note_env.username, "took_xinfa")
        assert result == "已拿"

    def test_profile_read_note_empty_returns_none(self, note_env):
        """profile_read() note 空文本返回 None"""
        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_upsert
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_read

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="")
        result = profile_read(note_env.username, "took_xinfa")
        assert result is None

    def test_profile_read_note_nonexistent_returns_none(self, note_env):
        """profile_read() 不存在的 note key 返回 None"""
        from lvjiang.apps.yysls.core.profile_engine.profile_ops import profile_read

        result = profile_read(note_env.username, "nonexistent_note_key")
        assert result is None


# ─── ProfileEngine tick 不处理 note ────────────────────────────


class TestNoteEngineTick:
    def test_engine_tick_ignores_note(self, note_env):
        """ProfileEngine._tick_user 不处理 note"""
        from unittest.mock import MagicMock

        from lvjiang.apps.yysls.config.user_profile import _load_config
        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.apps.yysls.core.profile_engine.profile_engine import ProfileEngine

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")

        # 构造最小可用 engine，实际执行 tick
        config = _load_config()
        engine = ProfileEngine.__new__(ProfileEngine)
        engine._tick_user(note_env.username, config)

        # tick 后 note 值不变
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "已拿"
        assert entry["value"] == 0


# ─── DSL profile_inc note 防护 ─────────────────────────────


class TestNoteProfileInc:
    def test_profile_inc_rejects_note_key(self, note_env):
        """profile_inc 对 note key 拒绝操作，不覆写文本"""
        from types import SimpleNamespace

        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.apps.yysls.workflows.builtins.profile_funcs import _profile_inc

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")

        engine = SimpleNamespace(run_username=note_env.username)
        result = _profile_inc(engine, "took_xinfa", 1)

        assert result == 0
        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "已拿"


# ─── DSL profile_set note 对 bool/0 的处理 ───────────────


class TestNoteProfileSetFalsy:
    def test_profile_set_note_false_clears(self, note_env):
        """profile_set(key, False) 清空备注而非存储 'False'"""
        from types import SimpleNamespace

        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.apps.yysls.workflows.builtins.profile_funcs import _profile_set

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "took_xinfa", False)

        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == ""

    def test_profile_set_note_zero_clears(self, note_env):
        """profile_set(key, 0) 清空备注而非存储 '0'"""
        from types import SimpleNamespace

        from lvjiang.apps.yysls.core.profile_engine.profile_db import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.apps.yysls.workflows.builtins.profile_funcs import _profile_set

        db_upsert(note_env.username, "note", "took_xinfa", 0, value_text="已拿")

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "took_xinfa", 0)

        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == ""

    def test_profile_set_note_string_stores(self, note_env):
        """profile_set(key, '已拿') 正常存储字符串"""
        from types import SimpleNamespace

        from lvjiang.apps.yysls.core.profile_engine.profile_db import db_read_entry
        from lvjiang.apps.yysls.workflows.builtins.profile_funcs import _profile_set

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "took_xinfa", "已拿")

        entry = db_read_entry(note_env.username, "note", "took_xinfa")
        assert entry["value_text"] == "已拿"


# ─── sync_targets 校验：note 不能作为同步目标 ────────────


class TestNoteSyncValidation:
    def test_validate_sync_targets_warns_note_target(self, note_env):
        """配置层校验：note key 作为 sync_targets 目标时发出警告"""
        import io

        from loguru import logger

        from lvjiang.apps.yysls.config.user_profile import _validate_sync_targets
        from lvjiang.apps.yysls.config.profile_models import (
            NoteKeyDef,
            StockKeyDef,
            SyncTargetDef,
        )

        keys_by_model = {
            "note": [NoteKeyDef(key="took_xinfa", label="是否拿心法")],
            "stock": [
                StockKeyDef(
                    key="res", label="资源",
                    sync_targets=[SyncTargetDef(key="note:took_xinfa")],
                ),
            ],
        }

        buf = io.StringIO()
        sink_id = logger.add(buf, level="WARNING")
        try:
            _validate_sync_targets(keys_by_model)
        finally:
            logger.remove(sink_id)

        output = buf.getvalue()
        assert "sync_targets" in output
        assert "note" in output
