"""en_US 词表卫生：不允许存在源码里已经不存在的死键。

真实事故：Profile 模块迁移（de835a12e）后主表残留 168 条 profile_core_XXX
枚举键与一批旧文案键，"en 比 zh 还多 26 条"——看起来翻译过、实际全是死键，
还会误导后续补翻译的人把力气花在不存在的问题上。

本测试与 tr 审计同一判定：键存在于 en_US.auto_strings，但整个 src 树的
字符串常量池里找不到。允许清单用于"键确实特殊"的例外，新增例外必须写注释。
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EN = ROOT / "config" / "i18n" / "en_US.yaml"

#: 已人工确认要保留的例外（当前为空；加入时必须写明原因）
ALLOWED_STALE: set[str] = set()


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


def test_en_us_has_no_stale_keys() -> None:
    table = (yaml.safe_load(EN.read_text(encoding="utf-8")) or {}).get("auto_strings") or {}
    pool = _literal_pool()
    stale = sorted(
        key for key in table
        if key not in pool and key not in ALLOWED_STALE
    )
    assert not stale, (
        f"en_US 存在 {len(stale)} 条死键（源码已无此文案）。"
        f"删除它们而不是继续翻译：{stale[:20]}"
    )
