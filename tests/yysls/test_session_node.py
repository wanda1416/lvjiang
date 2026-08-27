"""燕云会话数据从独立 yysls.json 迁到 session.json 的 ``yysls`` 节点。

旧实现里 play_styles 与 graduation_session 各自维护一份
``config/session/yysls.json`` 的加载与原子写（tempfile + os.replace 的样板
各抄一遍），且都是 load→改→save 的竞态写法。改走 SessionStore 的插件节点
后，并发安全由它的文件锁统一保证，运行态也集中在一个文件里。

兼容判据是**节点在不在**而不是**节点空不空**：若按"空就回退旧文件"来判，
用户把配置清空后旧数据会自己爬回来。
"""
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
    monkeypatch.setattr(session_node, "LEGACY_PATH", tmp_path / "yysls.json")
    return tmp_path


def _write_legacy(root, payload: dict) -> None:
    (root / "yysls.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _session(root) -> dict:
    path = root / "session.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TestLegacyCompatibility:
    def test_reads_legacy_file_when_node_absent(self, session_env):
        from lvjiang.apps.yysls.config.graduation_session import get_baseline_dps
        from lvjiang.apps.yysls.config.play_styles import get_play_styles

        _write_legacy(session_env, {
            "play_styles": {"破竹·樽": {"我的配置": {"atk": 100}}},
            "graduations": {"破竹·樽": {"基础方案": {"baseline_dps": 12345.0}}},
        })
        assert get_play_styles("破竹·樽") == {"我的配置": {"atk": 100}}
        assert get_baseline_dps("破竹·樽", "基础方案") == 12345.0

    def test_first_write_migrates_whole_legacy_payload(self, session_env):
        """首次写入以旧内容为基底——否则迁移会丢掉没被这次写到的那部分。"""
        from lvjiang.apps.yysls.config.play_styles import save_play_style

        _write_legacy(session_env, {
            "play_styles": {"破竹·樽": {"我的配置": {"atk": 100}}},
            "graduations": {"破竹·樽": {"基础方案": {"baseline_dps": 12345.0}}},
        })
        save_play_style("破竹·樽", "新配置", {"atk": 200})

        node = _session(session_env)["yysls"]
        assert sorted(node["play_styles"]["破竹·樽"]) == ["我的配置", "新配置"]
        assert node["graduations"]["破竹·樽"]["基础方案"]["baseline_dps"] == 12345.0

    def test_legacy_file_is_never_written(self, session_env):
        """只读兼容：降级回老版本仍应能读到自己原来的数据。"""
        from lvjiang.apps.yysls.config.play_styles import save_play_style

        _write_legacy(session_env, {"play_styles": {"破竹·樽": {"我的配置": {}}}})
        before = (session_env / "yysls.json").read_bytes()
        save_play_style("破竹·樽", "新配置", {"atk": 1})
        assert (session_env / "yysls.json").read_bytes() == before

    def test_emptied_node_does_not_resurrect_legacy(self, session_env):
        """判据是节点在不在，不是空不空——否则清空后旧数据会自己爬回来。"""
        from lvjiang.apps.yysls.config.play_styles import get_play_styles
        from lvjiang.core.config.session import get_session_store

        _write_legacy(session_env, {"play_styles": {"破竹·樽": {"我的配置": {}}}})
        get_session_store().set_node("yysls", {})
        assert get_play_styles("破竹·樽") == {}

    def test_corrupt_legacy_file_does_not_crash(self, session_env):
        from lvjiang.apps.yysls.config.play_styles import get_play_styles

        (session_env / "yysls.json").write_text("{ 坏的 json", encoding="utf-8")
        assert get_play_styles("破竹·樽") == {}


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
