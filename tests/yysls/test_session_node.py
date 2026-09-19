"""燕云会话数据统一存储在 session.json 的 ``yysls`` 节点。"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """把 session 目录指到 tmp_path，并重置 SessionStore 单例。"""
    import lvjiang.constants as constants
    import lvjiang.core.config.session as session_mod

    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(session_mod, "_store", None)
    return tmp_path


def _session(root) -> dict:
    path = root / "session.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


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

        set_baseline_dps("破竹·樽", "基础方案", 999.5)
        assert get_baseline_dps("破竹·樽", "基础方案") == 999.5
        clear_baseline_dps("破竹·樽", "基础方案")
        assert get_baseline_dps("破竹·樽", "基础方案") is None

    def test_both_modules_share_one_node(self, session_env):
        """两个模块写同一个节点，互相不能覆盖对方的键。"""
        from lvjiang.apps.yysls.config.graduation_session import set_baseline_dps
        from lvjiang.apps.yysls.config.play_styles import save_play_style

        save_play_style("破竹·樽", "A", {"atk": 1})
        set_baseline_dps("破竹·樽", "基础方案", 100.0)

        node = _session(session_env)["yysls"]
        assert "play_styles" in node and "graduations" in node



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
