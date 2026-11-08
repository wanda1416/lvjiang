"""词表卫生：不允许存在源码里已经不存在的 CJK 死键，也不允许它们再增长。

判定规则：只标记**含 CJK 字符**的键——这些才是 tr() 实际查找的中文原文。
纯 ASCII/标识符键（如 btn_add、profile_core_001）是旧 section 结构的
路径元素，_build_translation_map 按路径配对用，不是 tr() 的查找键。

真实事故：Profile 模块迁移后主表残留 168 条 profile_core_XXX 枚举键，
"en 比 zh 还多 26 条"——看起来翻译过、实际全是死键。

范围：核心层按全段落硬性归零（2026-11-08 清掉 486 条标识符自映射与 5 条
重构后残留的 Android 采集文案）；插件层还有 252/231 条 CJK 存量——其中大半
是自动抽取遗留的伪键（用下划线拼接整句），其余可能是远端游戏配置才驱动的
术语，删除前要确认来源，所以先用棘轮锁住只许下降。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from lvjiang.i18n import available_languages

ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = ROOT / "config" / "i18n"
APPS_DIR = I18N_DIR / "apps" / "yysls"

#: 已人工确认要保留的例外（加入时必须写明原因）
ALLOWED_STALE: set[str] = {
    # 配置分组名：由 config 下发，源码里没有字面量，运行时 tr(分组名)
    "general",
}

#: 插件层仍未被源码字面量引用的 CJK 存量（2026-11-08 排查值），只许下降。
MAX_APP_STALE_CJK = {"zh_CN": 252, "en_US": 231}

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


def test_app_tables_stale_cjk_do_not_grow() -> None:
    """插件层 CJK 死键只许减少：棘轮锁住 2026-11-08 的存量。"""
    pool = _literal_pool()
    grown = {}
    for code, limit in MAX_APP_STALE_CJK.items():
        count = len(_stale_cjk_keys(APPS_DIR / f"{code}.yaml", pool))
        if count > limit:
            grown[code] = f"{count} > {limit}"
    assert not grown, (
        f"插件层 CJK 死键上涨 {grown}——新增文案请先写进源码再翻译，"
        f"不要让词表自己长出键。修复存量后同步下调 MAX_APP_STALE_CJK。")


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
#: 出现两处显示不一致且无法从代码看出用哪条。基线是 2026-11-08 排查的存量
#: （核心 7 条、游戏插件 322 条），只许下降；修复时同步下调这里的数字。
MAX_SOURCE_CONFLICTS = {"core": 7, "apps/yysls": 322}


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
        "apps/yysls": _source_conflicts(
            _merged("zh_CN", I18N_DIR / "apps" / "yysls"),
            _merged("en_US", I18N_DIR / "apps" / "yysls"),
        ),
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
