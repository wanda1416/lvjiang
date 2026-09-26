"""燕云会话数据拆分与一次性迁移。"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """把 session 目录指到 tmp_path，并重置 SessionStore 单例。"""
    import lvjiang.constants as constants
    import lvjiang.core.config.session as session_mod
    from lvjiang.apps.yysls.config import session_node

    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(session_mod, "_store", None)
    session_node.reset_session_storage()
    return tmp_path


def _session(root) -> dict:
    path = root / "session.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _document(root, key: str) -> dict:
    return json.loads((root / "yysls" / f"{key}.json").read_text(encoding="utf-8"))


class TestRoundTrip:
    def test_play_style_crud(self, session_env):
        from lvjiang.apps.yysls.config.play_styles import (
            delete_play_style,
            get_play_styles,
            rename_play_style,
            save_play_style,
        )

        save_play_style("破竹·樽", "A", {"atk": 1})
        save_play_style("破竹·樽", "B", {"atk": 2})
        assert sorted(get_play_styles("破竹·樽")) == ["A", "B"]

        rename_play_style("破竹·樽", "A", "A2")
        assert sorted(get_play_styles("破竹·樽")) == ["A2", "B"]

        delete_play_style("破竹·樽", "B")
        assert sorted(get_play_styles("破竹·樽")) == ["A2"]

    def test_baseline_dps_set_and_clear(self, session_env):
        from lvjiang.apps.yysls.config.graduation_session import (
            clear_baseline_dps,
            get_baseline_dps,
            set_baseline_dps,
        )

        set_baseline_dps("破竹·樽", "基础方案", 110, 1, 999.5)
        assert get_baseline_dps("破竹·樽", "基础方案", 110, 1) == 999.5
        clear_baseline_dps("破竹·樽", "基础方案", 110, 1)
        assert get_baseline_dps("破竹·樽", "基础方案", 110, 1) is None

    def test_modules_write_separate_documents(self, session_env):
        """不同状态各写一个文档，不覆盖旁边的状态。"""
        from lvjiang.apps.yysls.config.graduation_session import set_baseline_dps
        from lvjiang.apps.yysls.config.play_styles import save_play_style

        save_play_style("破竹·樽", "A", {"atk": 1})
        set_baseline_dps("破竹·樽", "基础方案", 110, 1, 100.0)

        assert "破竹·樽" in _document(session_env, "play_styles")
        assert "破竹·樽" in _document(session_env, "graduations")
        assert "yysls" not in _session(session_env)


def test_legacy_migrates_once_and_preserves_unrelated_nodes(session_env, monkeypatch):
    from lvjiang.apps.yysls.config import session_node

    legacy = {
        "version": 2,
        "settings": {"theme": "dark"},
        "yysls": {
            "play_styles": {"school": {"style": {"atk": 1}}},
            "graduations": {"school": {"scheme": {"110": {"1": {"baseline_dps": 100}}}}},
            "attr_loadout": {"school": {"level": 110}},
            "attr_derivations": {"school": {"style": {"level": 110}}},
        },
    }
    (session_env / "session.json").write_text(json.dumps(legacy), encoding="utf-8")
    session_node.initialize_session_storage()
    assert _session(session_env) == {"version": 2, "settings": {"theme": "dark"}}
    for key, value in legacy["yysls"].items():
        assert _document(session_env, key) == value
    assert _document(session_env, "_meta") == {"version": 1}

    session_node.reset_session_storage()
    from lvjiang.core.config.session import SessionStore

    monkeypatch.setattr(
        SessionStore, "consume_node",
        lambda self, key, consumer: pytest.fail("重复访问旧节点"),
    )
    session_node.initialize_session_storage()
    assert session_node.load("play_styles") == legacy["yysls"]["play_styles"]


def test_partial_migration_retries_without_losing_legacy(session_env, monkeypatch):
    from lvjiang.apps.yysls.config import session_node

    old = {"play_styles": {"school": {"A": {"atk": 1}}}}
    (session_env / "session.json").write_text(
        json.dumps({"version": 2, "yysls": old}), encoding="utf-8",
    )
    original_write = session_node.YyslsSessionStore._write
    failed = False

    def fail_second_document(path, data):
        nonlocal failed
        if path.name == "graduations.json" and not failed:
            failed = True
            raise OSError("模拟迁移中断")
        original_write(path, data)

    monkeypatch.setattr(session_node.YyslsSessionStore, "_write", staticmethod(fail_second_document))
    with pytest.raises(OSError, match="模拟迁移中断"):
        session_node.initialize_session_storage()
    assert _session(session_env)["yysls"] == old
    assert _document(session_env, "play_styles") == old["play_styles"]

    monkeypatch.setattr(session_node.YyslsSessionStore, "_write", staticmethod(original_write))
    session_node.reset_session_storage()
    session_node.initialize_session_storage()
    assert "yysls" not in _session(session_env)
    assert _document(session_env, "play_styles") == old["play_styles"]


def test_unknown_legacy_key_is_not_discarded(session_env):
    from lvjiang.apps.yysls.config import session_node

    original = {"version": 2, "yysls": {"future_key": {"value": 1}}}
    (session_env / "session.json").write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="未登记子项"):
        session_node.initialize_session_storage()
    assert _session(session_env) == original
    assert not (session_env / "yysls" / "_meta.json").exists()


def test_conflicting_new_file_preserves_both_sides(session_env):
    from lvjiang.apps.yysls.config import session_node

    original = {"version": 2, "yysls": {"play_styles": {"school": {"old": {}}}}}
    (session_env / "session.json").write_text(json.dumps(original), encoding="utf-8")
    target = session_env / "yysls" / "play_styles.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"school": {"new": {}}}), encoding="utf-8")

    with pytest.raises(ValueError, match="冲突"):
        session_node.initialize_session_storage()
    assert _session(session_env) == original
    assert _document(session_env, "play_styles") == {"school": {"new": {}}}
    assert not (session_env / "yysls" / "_meta.json").exists()


def test_fresh_install_does_not_create_legacy_session_file(session_env):
    from lvjiang.apps.yysls.config import session_node

    session_node.initialize_session_storage()
    assert not (session_env / "session.json").exists()
    assert all(_document(session_env, key) == {} for key in session_node.DOCUMENT_FILES)
    assert _document(session_env, "_meta") == {"version": 1}


def test_missing_file_after_migration_is_not_treated_as_empty(session_env):
    from lvjiang.apps.yysls.config import session_node

    session_node.initialize_session_storage()
    (session_env / "yysls" / "play_styles.json").unlink()
    session_node.reset_session_storage()

    with pytest.raises(FileNotFoundError, match="play_styles.json"):
        session_node.initialize_session_storage()


def test_interrupted_two_document_write_recovers(session_env, monkeypatch):
    from lvjiang.apps.yysls.config import session_node
    from lvjiang.apps.yysls.config.play_styles import save_play_style

    store = session_node.get_store()
    store.ensure_initialized()
    original_write = store._write
    failed = False

    def fail_derivations(path, data):
        nonlocal failed
        if path.name == "attr_derivations.json" and not failed:
            failed = True
            raise OSError("模拟双文件写入中断")
        original_write(path, data)

    monkeypatch.setattr(store, "_write", fail_derivations)
    with pytest.raises(OSError, match="模拟双文件写入中断"):
        save_play_style("school", "A", {"atk": 1}, derivation={"level": 110})
    monkeypatch.setattr(store, "_write", original_write)
    session_node.reset_session_storage()
    assert session_node.load("play_styles")["school"]["A"] == {"atk": 1}
    assert session_node.load("attr_derivations")["school"]["A"] == {"level": 110}
    assert not (session_env / "yysls" / "_transaction.json").exists()



def test_manual_overwrite_clears_only_its_derivation(session_env):
    from types import SimpleNamespace

    from lvjiang.apps.yysls.config.attr_loadout import get_derivation
    from lvjiang.apps.yysls.config.play_styles import save_play_style
    from lvjiang.apps.yysls.ui.game_settings.attr_derive_panel import AttrDerivePanel

    school = "鸣金·虹"
    derivation = {"combat_delta": {"max_outer": 20.0}}
    save_play_style(school, "样本", {"max_outer": 120.0}, derivation=derivation)
    save_play_style(school, "其他", {"max_outer": 120.0}, derivation=derivation)
    host = SimpleNamespace(_combo_reference=SimpleNamespace(currentData=lambda: "样本"),
                           _school=lambda: school)
    assert AttrDerivePanel._reference_attrs(host).max_outer == 100.0
    save_play_style(school, "样本", {"max_outer": 100.0})
    assert get_derivation(school, "样本") == {}
    assert get_derivation(school, "其他") == derivation
    assert AttrDerivePanel._reference_attrs(host).max_outer == 100.0
