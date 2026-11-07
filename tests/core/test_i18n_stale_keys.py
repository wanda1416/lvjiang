"""en_US 词表卫生：不允许存在源码里已经不存在的 CJK 死键。

判定规则：只标记**含 CJK 字符**的键——这些才是 tr() 实际查找的中文原文。
纯 ASCII/标识符键（如 btn_add、profile_core_001）是旧 section 结构的
路径元素，_build_translation_map 按路径配对用，不是 tr() 的查找键。

真实事故：Profile 模块迁移后主表残留 168 条 profile_core_XXX 枚举键，
"en 比 zh 还多 26 条"——看起来翻译过、实际全是死键。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from lvjiang.i18n import available_languages

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = ROOT / "config" / "i18n"

#: 已人工确认要保留的例外（当前为空；加入时必须写明原因）
ALLOWED_STALE: set[str] = set()

_HAS_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _literal_pool() -> set[str]:
    pool: set[str] = set()
    for py in (ROOT / "src").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                pool.add(node.value)
    return pool


def _iter_auto_strings_tables() -> list[Path]:
    files = [I18N_DIR / "en_US.yaml", I18N_DIR / "zh_CN.yaml"]
    for lang_dir in (I18N_DIR / "en_US", I18N_DIR / "zh_CN"):
        if lang_dir.is_dir():
            files.extend(sorted(lang_dir.glob("*.yaml")))
    return files


def test_en_us_has_no_stale_cjk_keys() -> None:
    pool = _literal_pool()
    stale: dict[str, list[str]] = {}
    for path in _iter_auto_strings_tables():
        table = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
            "auto_strings") or {}
        for key in table:
            k = str(key)
            if not _HAS_CJK.search(k):
                continue
            if k in pool or k in ALLOWED_STALE:
                continue
            stale.setdefault(k, []).append(str(path.relative_to(ROOT)))
    assert not stale, (
        f"词表存在 {len(stale)} 条 CJK 死键（源码已无此中文文案）。"
        f"删除它们而不是继续翻译：{sorted(stale)[:20]}"
    )


def test_zh_and_en_key_sets_match_per_module() -> None:
    """zh 侧必须有 en 侧每个键的自映射，否则配对跳过、翻译静默失效。

    真实事故：宿主 1639 条翻译只写了 en 侧，zh 侧没有自映射键，
    配对器按路径取不到 zh 原文，英文界面下这批翻译全部不生效。
    """
    problems: list[str] = []
    en_dir = I18N_DIR / "en_US"
    zh_dir = I18N_DIR / "zh_CN"
    for en_file in sorted(en_dir.glob("*.yaml")):
        zh_file = zh_dir / en_file.name
        if not zh_file.exists():
            problems.append(f"{en_file.name}: zh 侧文件不存在")
            continue
        en_table = (yaml.safe_load(en_file.read_text(encoding="utf-8"))
                    or {}).get("auto_strings") or {}
        zh_doc = (yaml.safe_load(zh_file.read_text(encoding="utf-8"))
                  or {}).get("auto_strings") or {}
        missing = [str(k) for k in en_table if str(k) not in zh_doc]
        if missing:
            problems.append(f"{en_file.name}: zh 侧缺 {len(missing)} 条自映射")
    assert not problems, problems


def test_shipped_language_files_declare_display_names() -> None:
    """两套主文件的 meta.display_name 用于语言下拉框。"""
    names = {}
    for code in ("zh_CN", "en_US"):
        data = yaml.safe_load((I18N_DIR / f"{code}.yaml").read_text(encoding="utf-8"))
        names[code] = (data.get("meta") or {}).get("display_name")
    assert names == {"zh_CN": "简体中文", "en_US": "English"}


def test_available_languages_follows_directory(tmp_path, monkeypatch):
    missing = tmp_path / "i18n"
    monkeypatch.setattr("lvjiang.i18n.get_config_i18n_dir", lambda: missing)
    assert available_languages() == [{"code": "zh_CN", "name": "简体中文"}]

    missing.mkdir()
    (missing / "zh_CN.yaml").write_text(
        "meta:\n  display_name: 简体中文\n", encoding="utf-8")
    (missing / "en_US.yaml").write_text(
        "meta:\n  display_name: English\n", encoding="utf-8")
    found = {item["code"]: item["name"] for item in available_languages()}
    assert found == {"zh_CN": "简体中文", "en_US": "English"}
