"""yysls 插件词表（``config/i18n/apps/yysls``）的存量棘轮。

判定工具与核心层用例在 ``tests/core/test_i18n_stale_keys.py``；插件层的存量上限
属于本插件，只许下降，修复时同步下调这里的数字。词表格式（单行条目、可解析）由
``tests/core/test_i18n_table_format.py`` 统一覆盖全部词表，这里不再重复。
"""
from __future__ import annotations

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
