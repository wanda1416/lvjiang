"""基础属性的反推代次标记。

基础属性是「面板 - 装备」反推出来的。五维换算系数修正后，此前存下的
基础属性不再等于「面板 - 正确装备」；由于只存了反推结果、没存当时的
面板值与装备快照，无法自动补偿，只能请用户重填。版本号就是用来把
这些过期数据认出来的。
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


def test_newly_saved_base_attrs_carry_the_current_version(session_env) -> None:
    from lvjiang.apps.yysls.config.play_styles import (
        is_play_style_stale,
        save_play_style,
    )

    save_play_style("破竹·樽", "我的配置", {"min_outer": 2155.4})

    assert not is_play_style_stale("破竹·樽", "我的配置")


def test_base_attrs_without_a_version_are_treated_as_stale(session_env) -> None:
    """旧版本写的数据没有版本字段，必须判为过期而不是默认最新。"""
    from lvjiang.apps.yysls.config.play_styles import (
        get_play_style_version,
        is_play_style_stale,
    )

    (session_env / "yysls.json").write_text(
        json.dumps({"play_styles": {"破竹·樽": {"旧配置": {"min_outer": 2000.0}}}}),
        encoding="utf-8",
    )

    assert get_play_style_version("破竹·樽", "旧配置") == 1
    assert is_play_style_stale("破竹·樽", "旧配置")


def test_stale_listing_covers_every_school(session_env) -> None:
    from lvjiang.apps.yysls.config.play_styles import (
        save_play_style,
        stale_play_styles,
    )

    (session_env / "yysls.json").write_text(
        json.dumps({"play_styles": {
            "破竹·樽": {"旧配置": {"min_outer": 1.0}},
            "牵丝·玉": {"另一个旧配置": {"min_outer": 2.0}},
        }}),
        encoding="utf-8",
    )
    save_play_style("鸣金·虹", "新配置", {"min_outer": 3.0})

    assert sorted(stale_play_styles()) == [
        ("牵丝·玉", "另一个旧配置"), ("破竹·樽", "旧配置"),
    ]


def test_rename_keeps_the_version(session_env) -> None:
    """改名不该把一套刚填好的基础属性变成「需要重填」。"""
    from lvjiang.apps.yysls.config.play_styles import (
        is_play_style_stale,
        rename_play_style,
        save_play_style,
    )

    save_play_style("破竹·樽", "旧名", {"min_outer": 2155.4})
    rename_play_style("破竹·樽", "旧名", "新名")

    assert not is_play_style_stale("破竹·樽", "新名")


def test_delete_drops_the_version_too(session_env) -> None:
    """版本不能留在已删除的名字上——否则外部导入一套同名旧数据会被
    误判成已是最新，从而不提示重填。"""
    from lvjiang.apps.yysls.config.play_styles import (
        delete_play_style,
        get_play_style_version,
        save_play_style,
    )

    save_play_style("破竹·樽", "配置", {"min_outer": 2155.4})
    delete_play_style("破竹·樽", "配置")

    assert get_play_style_version("破竹·樽", "配置") == 1
