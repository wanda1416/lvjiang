"""三级插件体系的发现与加载回归。

保护的契约：

- 内置根与外置默认根都按「子目录 + ``__init__.py``」扫描，非法目录名只跳过；
- ``-reg NAME@PATH`` 能把任意目录挂载成 ``lvjiang.apps.NAME`` 并加载其 hooks；
- ``--all-plugins`` 在显式 ``-reg`` 之后追加扫描结果，且同名只出现一次。

这些契约目前没有其它回归网：``-reg`` 参数解析此前完全没有测试覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import lvjiang.apps as apps
from lvjiang.apps import (
    app_name,
    discover_app_ids,
    load_app,
    parse_app_spec,
    resolve_app_specs,
)


def _write_plugin(root: Path, directory_name: str, plugin_id: str) -> Path:
    directory = root / directory_name
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text(
        "from lvjiang.apps.base import AppHooks\n\n"
        f"hooks = AppHooks(id={plugin_id!r})\n",
        encoding="utf-8",
    )
    return directory


def test_discover_app_ids_includes_builtin_yysls():
    assert "yysls" in discover_app_ids()


def test_builtin_app_ids_in_sync_with_source_tree():
    """内置插件名册常量必须与实际源码目录一致。

    新增内置插件（src/lvjiang/apps/<name>/__init__.py）却忘记更新
    ``_BUILTIN_APP_IDS`` 时此用例失败，提醒同步登记；反之名册里登记了
    不存在（或已删除/拼错）的 ID 也会失败。frozen 环境下没有可扫的源码
    目录，跳过比对。
    """
    builtin_root = apps._BUILTIN_ROOT
    if not builtin_root.is_dir():
        pytest.skip("frozen 环境无内置插件源码目录，名册无法与磁盘比对")
    actual = apps._scan_root(builtin_root)
    assert tuple(sorted(actual)) == tuple(sorted(apps._BUILTIN_APP_IDS)), (
        "内置插件名册 _BUILTIN_APP_IDS 与源码目录不一致："
        f"源码目录={sorted(actual)}，名册={sorted(apps._BUILTIN_APP_IDS)}。"
        "新增内置插件请同步更新 src/lvjiang/apps/__init__.py 的 _BUILTIN_APP_IDS"
    )


def test_scan_skips_non_plugin_entries(tmp_path, monkeypatch):
    _write_plugin(tmp_path, "good_plugin", "good_plugin")
    (tmp_path / "not_a_plugin").mkdir()  # 缺 __init__.py
    _write_plugin(tmp_path, "bad-name", "bad")  # 非法标识符
    monkeypatch.setattr(apps, "_DEFAULT_PLUGINS_ROOT", tmp_path)

    names = apps.discover_app_ids()

    assert "good_plugin" in names
    assert "not_a_plugin" not in names
    assert "bad-name" not in names


def test_explicit_path_mounts_arbitrary_directory(tmp_path, monkeypatch):
    directory = _write_plugin(tmp_path, "any-dir-name", "any_dir")
    monkeypatch.delitem(sys.modules, "lvjiang.apps.any_dir", raising=False)

    hooks = load_app(f"any_dir@{directory}")

    assert hooks.id == "any_dir"
    assert app_name(f"any_dir@{directory}") == "any_dir"


def test_missing_explicit_path_reports_clear_error(tmp_path):
    with pytest.raises(KeyError, match="__init__"):
        load_app(f"nowhere@{tmp_path / 'absent'}")


def test_resolve_app_specs_keeps_order_and_appends_scanned(tmp_path, monkeypatch):
    _write_plugin(tmp_path, "extra_plugin", "extra_plugin")
    monkeypatch.setattr(apps, "_DEFAULT_PLUGINS_ROOT", tmp_path)

    ordered = resolve_app_specs(["yysls", "yysls"], all_plugins=True)

    assert ordered[0] == "yysls"  # 显式项在前
    assert ordered.count("yysls") == 1  # 同名去重
    assert "extra_plugin" in ordered  # 扫描结果被追加


def test_parse_app_spec_rejects_bad_name():
    with pytest.raises(KeyError, match="无效的插件名"):
        parse_app_spec("bad-name")
