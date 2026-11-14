"""词表卫生：不允许存在源码里已经不存在的 CJK 死键，也不允许它们再增长。

判定规则：只标记**含 CJK 字符**的键——这些才是 tr() 实际查找的中文原文。
纯 ASCII/标识符键（如 btn_add、profile_core_001）是旧 section 结构的
路径元素，_build_translation_map 按路径配对用，不是 tr() 的查找键。

真实事故：Profile 模块迁移后主表残留 168 条 profile_core_XXX 枚举键，
"en 比 zh 还多 26 条"——看起来翻译过、实际全是死键。

范围：核心层按全段落硬性归零（2026-11-08 清掉 486 条标识符自映射与 5 条
重构后残留的 Android 采集文案）。插件词表（``config/i18n/apps/<插件>``）的存量
棘轮属于各插件自己，在插件测试目录里维护（如 ``tests/yysls/test_i18n_tables.py``），
本文件只提供判定工具与核心层用例。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from lvjiang.i18n import available_languages
from tests.source_scan import source_files, string_constants

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = ROOT / "config" / "i18n"

#: 已人工确认要保留的例外（加入时必须写明原因）
ALLOWED_STALE: set[str] = {
    # 配置分组名：由 config 下发，源码里没有字面量，运行时 tr(分组名)
    "general",
}

_HAS_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _literal_pool() -> set[str]:
    pool: set[str] = set()
    for py in source_files():
        pool |= string_constants(py)
    return pool


def _stale_cjk_keys(path: Path, pool: set[str]) -> set[str]:
    """该词表文件全部段落里，源码已无对应文案的 CJK 键。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {key for key in _leaf_keys(data)
            if _HAS_CJK.search(key) and key not in pool and key not in ALLOWED_STALE}


def _leaf_keys(node: object) -> set[str]:
    if not isinstance(node, dict):
        return set()
    keys: set[str] = set()
    for key, value in node.items():
        if isinstance(value, dict):
            keys |= _leaf_keys(value)
        else:
            keys.add(str(key))
    return keys


def test_core_tables_have_no_stale_cjk_keys() -> None:
    """核心层每个段落（不只 auto_strings）都不允许残留 CJK 死键。

    只查 auto_strings 等于给命名段落开后门：词表拆成 settings_dialog/
    scene_editor 等段落后，旧用例对核心层已完全空转（2026-11-08 发现并修正）。
    """
    pool = _literal_pool()
    stale: dict[str, list[str]] = {}
    for code in ("zh_CN", "en_US"):
        for path in sorted((I18N_DIR / code).glob("*.yaml")):
            for key in _stale_cjk_keys(path, pool):
                stale.setdefault(key, []).append(str(path.relative_to(ROOT)))
    assert not stale, (
        f"核心词表存在 {len(stale)} 条 CJK 死键（源码已无此中文文案）。"
        f"删除它们而不是继续翻译：{sorted(stale)[:20]}"
    )


def _leaf_paths(data: object, trail: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """键路径集合（跳过多模块文件顶层的 meta 段）。"""
    if not isinstance(data, dict):
        return set()
    paths: set[tuple[str, ...]] = set()
    for key, value in data.items():
        if not trail and key == "meta":
            continue
        if isinstance(value, dict):
            paths |= _leaf_paths(value, trail + (str(key),))
        else:
            paths.add(trail + (str(key),))
    return paths


def test_zh_and_en_key_sets_match_per_module() -> None:
    """zh 侧必须有 en 侧每个键的自映射，否则配对跳过、翻译静默失效。

    真实事故：宿主 1639 条翻译只写了 en 侧，zh 侧没有自映射键，
    配对器按路径取不到 zh 原文，英文界面下这批翻译全部不生效。

    检查覆盖每个文件的全部段落，不只是 auto_strings：词表拆成
    settings_dialog/scene_editor 等命名段落后，只查 auto_strings 等于什么都
    没查（2026-11-08 核对发现该用例当时已退化）。
    """
    problems: list[str] = []
    en_dir = I18N_DIR / "en_US"
    zh_dir = I18N_DIR / "zh_CN"
    for en_file in sorted(en_dir.glob("*.yaml")):
        zh_file = zh_dir / en_file.name
        if not zh_file.exists():
            problems.append(f"{en_file.name}: zh 侧文件不存在")
            continue
        en_paths = _leaf_paths(yaml.safe_load(en_file.read_text(encoding="utf-8")) or {})
        zh_paths = _leaf_paths(yaml.safe_load(zh_file.read_text(encoding="utf-8")) or {})
        missing = sorted("/".join(path) for path in en_paths - zh_paths)
        if missing:
            problems.append(
                f"{en_file.name}: zh 侧缺 {len(missing)} 条自映射（如 {missing[:3]}）")
    assert not problems, problems


#: 同源两译：同一句中文在不同段落配了不同译法时，扁平查表由后加载者静默取胜，
#: 出现两处显示不一致且无法从代码看出用哪条。基线是 2026-11-08 排查的核心层存量
#: （7 条），只许下降；修复时同步下调这里的数字。插件词表的上限由插件测试维护。
MAX_SOURCE_CONFLICTS = {"core": 7}


def _merged(language: str, base: Path = I18N_DIR) -> dict:
    """按加载器的规则合并主文件与 {lang}/ 模块文件。

    镜像 ``lvjiang.i18n._load_translation_file`` 的合并方式（同名顶层段落
    浅合并、目录按文件名排序覆盖主文件），冲突统计才有意义。
    """
    data = yaml.safe_load((base / f"{language}.yaml").read_text(encoding="utf-8")) or {}
    lang_dir = base / language
    if lang_dir.is_dir():
        for module_file in sorted(lang_dir.glob("*.yaml")):
            module_data = yaml.safe_load(module_file.read_text(encoding="utf-8")) or {}
            for key, value in module_data.items():
                if isinstance(value, dict) and isinstance(data.get(key), dict):
                    data[key].update(value)
                else:
                    data[key] = value
    return data


def _source_conflicts(zh_data: dict, en_data: dict) -> dict[str, set[str]]:
    """同一句中文原文映射到不同译文的冲突项。"""
    first: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}

    def walk(zh: object, en: object, trail: tuple[str, ...] = ()) -> None:
        if not isinstance(en, dict):
            return
        for key, en_value in en.items():
            if not trail and key == "meta":
                continue
            zh_value = zh.get(key) if isinstance(zh, dict) else None
            if isinstance(en_value, dict) and isinstance(zh_value, dict):
                walk(zh_value, en_value, trail + (str(key),))
            elif isinstance(zh_value, str) and isinstance(en_value, str):
                if zh_value in first and first[zh_value] != en_value:
                    conflicts.setdefault(zh_value, set()).update({first[zh_value], en_value})
                first.setdefault(zh_value, en_value)

    walk(zh_data, en_data)
    return conflicts


def test_duplicate_source_translations_do_not_grow() -> None:
    """同源两译只能减少，不能增加。

    真实事故：auto_strings 里 52 条未翻译占位条目盖掉了前面写好的手工译文，
    同一句中文最终显示哪条全看加载顺序。
    """
    layers = {
        "core": _source_conflicts(_merged("zh_CN"), _merged("en_US")),
    }
    grown = {
        name: sorted(conflicts)[:5]
        for name, conflicts in layers.items()
        if len(conflicts) > MAX_SOURCE_CONFLICTS[name]
    }
    assert not grown, (
        "同源两译数量上涨（上限 %s）：%s——同一句中文不要在两处配不同译法，"
        "请复用已有条目或改到同一处。" % (MAX_SOURCE_CONFLICTS, grown))


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


def _literal_pool_split() -> tuple[set[str], set[str]]:
    """(宿主源码字面量, 插件源码字面量)：插件 = ``src/lvjiang/apps/**``。"""
    apps_root = ROOT / "src" / "lvjiang" / "apps"
    host: set[str] = set()
    plugin: set[str] = set()
    for py in source_files():
        bucket = plugin if apps_root in py.parents else host
        bucket |= string_constants(py)
    return host, plugin


def test_core_tables_do_not_carry_plugin_only_keys() -> None:
    """核心词表不替插件维护文案：只被插件源码使用的键必须放在插件词表。

    2026-11-14 依赖反转清理：核心 common.yaml 里 113 条只给插件用的条目
    （插件词表已有同键，属重复残留）整体删除，这里防止再长回来。
    """
    host, plugin = _literal_pool_split()
    offenders: dict[str, list[str]] = {}
    for code in ("zh_CN", "en_US"):
        for path in sorted((I18N_DIR / code).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for key in _leaf_keys(data):
                if _HAS_CJK.search(key) and key in plugin and key not in host:
                    offenders.setdefault(key, []).append(str(path.relative_to(ROOT)))
    assert not offenders, (
        f"核心词表有 {len(offenders)} 条只被插件使用的文案，请移到 "
        f"config/i18n/apps/<app>/：{sorted(offenders)[:20]}")
