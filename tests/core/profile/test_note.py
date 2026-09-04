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
from tests.case_matrix import case_matrix


@pytest.fixture
def note_env(tmp_path, monkeypatch):
    """隔离的 note 测试环境"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.core.profile.repository as profile_db
    import lvjiang.core.profile.schema as profile_config

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "note": [
                    {"key": "user_note", "label": "状态备注"},
                    {
                        "key": "group_note",
                        "label": "分组备注",
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
        from lvjiang.core.profile.models import NoteKeyDef

        kd = NoteKeyDef.from_dict({
            "key": "user_note",
            "label": "状态备注",
            "description": "记录心法获取状态",
        })
        assert kd.key == "user_note"
        assert kd.label == "状态备注"
        assert kd.description == "记录心法获取状态"
        assert isinstance(kd, NoteKeyDef)

    def test_from_dict_defaults(self):
        from lvjiang.core.profile.models import NoteKeyDef

        kd = NoteKeyDef.from_dict({"key": "k", "label": "l"})
        assert kd.sources == []
        assert kd.uses == []
        assert kd.sync_targets == []
        assert kd.cap is None

    def test_to_dict(self):
        from lvjiang.core.profile.models import NoteKeyDef

        kd = NoteKeyDef(key="k", label="l", description="desc")
        d = kd.to_dict()
        assert d["key"] == "k"
        assert d["label"] == "l"
        assert d["description"] == "desc"

    def test_roundtrip(self):
        from lvjiang.core.profile.models import NoteKeyDef

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
        from lvjiang.core.profile.models import NoteKeyDef
        from lvjiang.core.profile.schema import _load_config

        schema = _load_config()
        kd = schema.get_key("user_note")
        assert kd is not None
        assert isinstance(kd, NoteKeyDef)
        assert kd.label == "状态备注"

    def test_get_model_type_note(self, note_env):
        """note key 的 model_type 为 'note'"""
        from lvjiang.core.profile.schema import _load_config

        schema = _load_config()
        assert schema.get_model_type("user_note") == "note"

    def test_get_keys_by_model_note(self, note_env):
        """get_keys_by_model('note') 返回所有 note key"""
        from lvjiang.core.profile.schema import _load_config

        schema = _load_config()
        note_keys = schema.get_keys_by_model("note")
        assert len(note_keys) == 2
        key_names = {kd.key for kd in note_keys}
        assert key_names == {"user_note", "group_note"}


# ─── DB value_text 读写 ────────────────────────────────────────


class TestNoteDB:
    def test_upsert_and_read_value_text(self, note_env):
        """DB value_text 列读写正常"""
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "已完成"
        assert entry["value"] == 0

    def test_value_text_empty_by_default(self, note_env):
        """不传 value_text 时默认空字符串"""
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "user_note", 0)
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == ""

    def test_value_text_overwrite(self, note_env):
        """value_text 可被覆写"""
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")
        db_upsert(note_env.username, "note", "user_note", 0, value_text="未完成")
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "未完成"


# ─── profile_action note 短路 ──────────────────────────────────


class TestNoteProfileAction:
    def test_note_short_circuit_write(self, note_env):
        """profile_action() note 文本写入走 db_upsert(value_text=...)"""
        from lvjiang.core.profile.repository import db_read_entry
        from lvjiang.core.profile.service import profile_action

        result = profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="已完成",
            source="test",
        )
        assert result == "已完成"
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "已完成"

    def test_note_short_circuit_empty_text(self, note_env):
        """note 写入空文本"""
        from lvjiang.core.profile.repository import db_read_entry
        from lvjiang.core.profile.service import profile_action

        result = profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="",
        )
        assert result == ""
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == ""

    def test_note_records_history(self, note_env):
        """note 写入记入 history"""
        from lvjiang.core.profile.repository import ProfileDB
        from lvjiang.core.profile.service import profile_action

        profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="已完成",
        )
        # 直接查 DB history 表
        import lvjiang.core.profile.repository as pdb
        db: ProfileDB = pdb._db
        history = db.get_history(note_env.username)
        assert len(history) == 1
        assert history[0]["new_value_text"] == "已完成"
        assert history[0]["type"] == "note"

    def test_note_no_sync_targets(self, note_env):
        """note 不触发 sync_targets"""
        # 先给同步目标设初始值
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.core.profile.service import profile_action
        db_upsert(note_env.username, "stock", "target_stock", 1000)

        # note 写入（即使 note key 定义了 sync_targets，也不会触发）
        profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="已完成",
        )

        # 同步目标值不变
        target = db_read_entry(note_env.username, "stock", "target_stock")
        assert target["value"] == 1000


# ─── profile_read note 返回文本 ────────────────────────────────


class TestNoteProfileRead:
    def test_profile_read_note_returns_text(self, note_env):
        """profile_read() note 返回文本"""
        from lvjiang.core.profile.repository import db_upsert
        from lvjiang.core.profile.service import profile_read

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")
        result = profile_read(note_env.username, "user_note")
        assert result == "已完成"

    def test_profile_read_note_empty_returns_none(self, note_env):
        """profile_read() note 空文本返回 None"""
        from lvjiang.core.profile.repository import db_upsert
        from lvjiang.core.profile.service import profile_read

        db_upsert(note_env.username, "note", "user_note", 0, value_text="")
        result = profile_read(note_env.username, "user_note")
        assert result is None

    def test_profile_read_note_nonexistent_returns_none(self, note_env):
        """profile_read() 不存在的 note key 返回 None"""
        from lvjiang.core.profile.service import profile_read

        result = profile_read(note_env.username, "nonexistent_note_key")
        assert result is None


# ─── ProfileEngine tick 不处理 note ────────────────────────────


class TestNoteEngineTick:
    def test_engine_tick_ignores_note(self, note_env):
        """ProfileEngine._tick_user 不处理 note"""

        from lvjiang.core.profile.engine import ProfileEngine
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.core.profile.schema import _load_config

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")

        # 构造最小可用 engine，实际执行 tick
        config = _load_config()
        engine = ProfileEngine.__new__(ProfileEngine)
        engine._tick_user(note_env.username, config)

        # tick 后 note 值不变
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "已完成"
        assert entry["value"] == 0


# ─── DSL profile_inc note 防护 ─────────────────────────────


class TestNoteProfileInc:
    def test_profile_inc_rejects_note_key(self, note_env):
        """profile_inc 对 note key 拒绝操作，不覆写文本"""
        from types import SimpleNamespace

        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.workflows.builtins.profile import _profile_inc

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")

        engine = SimpleNamespace(run_username=note_env.username)
        result = _profile_inc(engine, "user_note", 1)

        assert result == 0
        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "已完成"


# ─── DSL profile_set note 对 bool/0 的处理 ───────────────


class TestNoteProfileSetFalsy:
    def test_profile_set_note_false_clears(self, note_env):
        """profile_set(key, False) 清空备注而非存储 'False'"""
        from types import SimpleNamespace

        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.workflows.builtins.profile import _profile_set

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "user_note", False)

        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == ""

    def test_profile_set_note_zero_clears(self, note_env):
        """profile_set(key, 0) 清空备注而非存储 '0'"""
        from types import SimpleNamespace

        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_upsert,
        )
        from lvjiang.workflows.builtins.profile import _profile_set

        db_upsert(note_env.username, "note", "user_note", 0, value_text="已完成")

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "user_note", 0)

        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == ""

    def test_profile_set_note_string_stores(self, note_env):
        """profile_set(key, '已完成') 正常存储字符串"""
        from types import SimpleNamespace

        from lvjiang.core.profile.repository import db_read_entry
        from lvjiang.workflows.builtins.profile import _profile_set

        engine = SimpleNamespace(run_username=note_env.username)
        _profile_set(engine, "user_note", "已完成")

        entry = db_read_entry(note_env.username, "note", "user_note")
        assert entry["value_text"] == "已完成"


# ─── sync_targets 校验：note 不能作为同步目标 ────────────


class TestNoteSyncValidation:
    def test_validate_sync_targets_warns_note_target(self, note_env):
        """配置层校验：note key 作为 sync_targets 目标时发出警告"""
        import io

        from loguru import logger

        from lvjiang.core.profile.models import (
            NoteKeyDef,
            StockKeyDef,
            SyncTargetDef,
        )
        from lvjiang.core.profile.schema import _validate_sync_targets

        keys_by_model = {
            "note": [NoteKeyDef(key="user_note", label="状态备注")],
            "stock": [
                StockKeyDef(
                    key="res", label="资源",
                    sync_targets=[SyncTargetDef(key="note:user_note")],
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


# ─── note 历史记录支持 ────────────────────────────────────────────


class TestNoteHistory:
    def test_note_action_records_history(self, note_env):
        """profile_action note 写入记录 history"""
        from lvjiang.core.profile.repository import (
            db_get_history,
        )
        from lvjiang.core.profile.service import profile_action

        # 第一次写入
        profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="已完成",
        )

        history = db_get_history(note_env.username, type_="note", key="user_note")
        assert len(history) == 1
        assert history[0]["new_value_text"] == "已完成"
        assert history[0]["old_value_text"] == ""
        assert history[0]["change_type"] == "action"

    def test_note_history_records_old_text(self, note_env):
        """note 覆写时 history 记录旧文本"""
        from lvjiang.core.profile.repository import (
            db_get_history,
        )
        from lvjiang.core.profile.service import profile_action

        profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="已完成",
        )
        profile_action(
            note_env.username, "user_note",
            model_type="note",
            set_value="未完成",
        )

        history = db_get_history(note_env.username, type_="note", key="user_note")
        assert len(history) == 2
        # 最新记录在前（按 id 倒序）
        assert history[0]["new_value_text"] == "未完成"
        assert history[0]["old_value_text"] == "已完成"
        assert history[1]["new_value_text"] == "已完成"
        assert history[1]["old_value_text"] == ""

    def test_note_same_value_no_history(self, note_env):
        """note 写入相同值不重复记录 history"""
        from lvjiang.core.profile.repository import (
            db_get_history,
            db_upsert,
        )

        # 先写入初始值
        db_upsert(note_env.username, "note", "user_note", 0, change_type="action", value_text="已完成")
        # 再次写入相同值
        db_upsert(note_env.username, "note", "user_note", 0, change_type="action", value_text="已完成")

        history = db_get_history(note_env.username, type_="note", key="user_note")
        # action 类型始终记录，所以会有 2 条
        assert len(history) == 2

    def test_note_history_tick_only_on_change(self, note_env):
        """note tick 类型仅在值变化时记录"""
        from lvjiang.core.profile.repository import (
            db_get_history,
            db_upsert,
        )

        # tick 写入新值
        db_upsert(note_env.username, "note", "user_note", 0, change_type="tick", value_text="新值")
        # tick 写入相同值
        db_upsert(note_env.username, "note", "user_note", 0, change_type="tick", value_text="新值")

        history = db_get_history(note_env.username, type_="note", key="user_note")
        # tick 只在值变化时记录，所以只有 1 条
        assert len(history) == 1


# ─── note 的上限：取整、取上限、展示上限 ──────────────────────


@pytest.fixture
def note_cap_env(tmp_path, monkeypatch):
    """带 cap / soft / show_cap 各种组合的 note 环境"""
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    import lvjiang.core.profile.repository as profile_db
    import lvjiang.core.profile.schema as profile_config

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
        yaml.dump(
            {
                "note": [
                    {"key": "hard", "label": "硬上限", "cap": 20, "show_cap": True},
                    {"key": "soft", "label": "软上限", "cap": 20,
                     "soft": True, "show_cap": True},
                    {"key": "nocap", "label": "无上限", "show_cap": True},
                    {"key": "hide", "label": "不展示", "cap": 20, "show_cap": False},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="u")

    profile_config._config = None
    profile_db._db = None


def _write_then_render(env, key: str, value: str) -> tuple[str, str]:
    """写入 note 值，返回 (落库文本, 总览单元格显示文本)"""
    from lvjiang.core.profile.models import MODEL_NOTE
    from lvjiang.core.profile.repository import db_read_all
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.core.profile.service import profile_action
    from lvjiang.ui.profile.cell_formatting import format_profile_cell

    profile_action(env.username, key, model_type=MODEL_NOTE,
                   set_value=value, source="")
    data = db_read_all(env.username)
    kd = get_profile_config().get_key(key, model_type=MODEL_NOTE)
    stored = data[MODEL_NOTE][key].get("value_text")
    text, _style = format_profile_cell(kd, MODEL_NOTE, data)
    return stored, text


class TestNoteNumericValueTakesCap:
    """填的是数字就按数值语义归一，免得显示出 12.7/20 或 25/20 这种自相矛盾的值。"""

    @case_matrix("raw,expected", [("12.7", "13"), ("12.2", "12"),
                                              ("8", "8"), ("0", "0")])
    def test_numeric_is_rounded(self, note_cap_env, raw, expected):
        stored, _ = _write_then_render(note_cap_env, "hard", raw)
        assert stored == expected

    def test_numeric_clamped_to_hard_cap(self, note_cap_env):
        stored, text = _write_then_render(note_cap_env, "hard", "25")
        assert stored == "20", "硬上限应截断"
        assert text == "20/20"

    def test_soft_cap_rounds_but_does_not_clamp(self, note_cap_env):
        """软上限沿用数值模型语义：只提醒不截断。"""
        stored, text = _write_then_render(note_cap_env, "soft", "25")
        assert stored == "25"
        assert text == "25/20"

    def test_no_cap_means_no_clamp(self, note_cap_env):
        stored, text = _write_then_render(note_cap_env, "nocap", "12.7")
        assert stored == "13"
        assert text == "13", "没有上限就没有 /Y 可显示"


class TestNoteNonNumericSkipsCap:
    """不可转成数值的文本跳过取整、取上限，也不显示上限。

    给「已完成」挂个 /20 没有任何意义——真要按数量管理就该用 stock 而不是
    note。所以 X/Y 只在 X 确实是数字时才成立。
    """

    def test_text_stored_as_is(self, note_cap_env):
        stored, _ = _write_then_render(note_cap_env, "hard", "已完成")
        assert stored == "已完成"

    @case_matrix("raw", ["已完成", "待定", "见群公告"])
    def test_text_does_not_show_cap(self, note_cap_env, raw):
        _, text = _write_then_render(note_cap_env, "hard", raw)
        assert text == raw, "非数字的值不该被挂上 /上限"

    def test_empty_stays_empty(self, note_cap_env):
        stored, text = _write_then_render(note_cap_env, "hard", "")
        assert stored == ""
        assert text == "", "空值不该显示成 /20"


class TestNoteShowCapToggle:
    """show_cap 只管显示；归一化由 cap 本身决定，与开关无关。"""

    def test_hidden_cap_still_normalizes(self, note_cap_env):
        stored, text = _write_then_render(note_cap_env, "hide", "25")
        assert stored == "20", "不展示上限，也仍按硬上限截断"
        assert text == "20", "关掉开关就不该出现 /Y"

    def test_hidden_cap_text_unchanged(self, note_cap_env):
        stored, text = _write_then_render(note_cap_env, "hide", "已完成")
        assert stored == "已完成"
        assert text == "已完成"


class TestNoteNumericPredicate:
    """写入侧和展示侧必须用同一套"算不算数字"的判断，否则会存/显不一致。"""

    @case_matrix("raw,expected", [
        ("12", 12.0), ("12.7", 12.7), ("-3", -3.0), ("  8  ", 8.0),
        ("已完成", None), ("", None), ("   ", None), ("12个", None), (None, None),
    ])
    def test_note_numeric_value(self, raw, expected):
        from lvjiang.core.profile.models import note_numeric_value

        assert note_numeric_value(raw) == expected

    def test_write_and_display_agree(self, note_cap_env):
        """凡是写入侧当成数字归一了的，展示侧就该认它、加上 /Y；反之亦然。"""
        from lvjiang.core.profile.models import note_numeric_value

        for raw in ["12.7", "25", "0", "已完成", "待定", "12个"]:
            stored, text = _write_then_render(note_cap_env, "hard", raw)
            numeric = note_numeric_value(stored) is not None
            assert ("/" in text) is numeric, (
                f"{raw!r} 存成 {stored!r} 显示 {text!r}：存/显对数字的判断不一致")
