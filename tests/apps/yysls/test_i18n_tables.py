"""yysls 插件词表（``config/i18n/apps/yysls``）的存量棘轮。

判定工具与核心层用例在 ``tests/core/test_i18n_stale_keys.py``；插件层的存量上限
属于本插件，只许下降，修复时同步下调这里的数字。
"""
from __future__ import annotations

import yaml

from tests.core.test_i18n_stale_keys import (
    I18N_DIR,
    _literal_pool,
    _merged,
    _source_conflicts,
    _stale_cjk_keys,
)

APPS_DIR = I18N_DIR / "apps" / "yysls"

#: 插件层仍未被源码字面量引用的 CJK 存量（2026-11-08 排查值），只许下降。
#: 其中大半是自动抽取遗留的伪键（用下划线拼接整句），其余可能是远端游戏配置
#: 才驱动的术语，删除前要确认来源。
MAX_APP_STALE_CJK = {"zh_CN": 252, "en_US": 231}

#: 同源两译存量（2026-11-08 排查值 322 条），只许下降。
MAX_SOURCE_CONFLICTS = 322

#: 多行显式键（``? '…'``）必须为 0：文案里的换行要用 ``\n`` 转义写成单行。
MAX_MULTILINE_KEYS = 0


def test_language_tables_parse() -> None:
    """12 个语言表必须都能被 YAML 解析。

    值以 ``{`` 开头（例如 ``{site} 形式でエクスポート``）时必须加引号，否则整份
    词表解析失败——那种错误只在该语言的用户启动时才会暴露，这里提前拦住。
    """
    broken = {}
    for path in sorted(APPS_DIR.glob("*.yaml")):
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            broken[path.name] = str(exc).splitlines()[0]
    assert not broken, f"词表无法解析：{broken}"


def test_no_multiline_yaml_entries() -> None:
    """词表不能出现多行显式键（``? '…'``）。

    一句话里有换行时，写成 ``"第一行\\n第二行"`` 这种双引号单行条目——YAML 会把
    ``\\n`` 解释成换行，key 与源码字面量仍然相等。生成器若又摊成多行，这条会失败，
    用 ``scripts/one_off/normalize_i18n_newlines.py`` 归一。
    """
    offenders = {}
    for path in sorted(APPS_DIR.glob("*.yaml")):
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("  ? "))
        if count:
            offenders[path.name] = count
    assert not offenders, (
        f"词表出现多行条目 {offenders}（上限 {MAX_MULTILINE_KEYS}）："
        f"文案里的换行请写成 \\n 转义的单行条目。")


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


def test_duplicate_source_translations_do_not_grow() -> None:
    """同源两译只能减少，不能增加（插件层）。"""
    conflicts = _source_conflicts(_merged("zh_CN", APPS_DIR), _merged("en_US", APPS_DIR))
    assert len(conflicts) <= MAX_SOURCE_CONFLICTS, (
        f"插件层同源两译数量上涨（{len(conflicts)} > {MAX_SOURCE_CONFLICTS}）："
        f"{sorted(conflicts)[:5]}——同一句中文不要在两处配不同译法。")
