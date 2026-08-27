"""代码里的 ``tr()`` 与翻译表的一致性。

现有 test_i18n.py 只覆盖**机制**（回退、并行遍历、结构不对齐、meta 跳过），
没有一条检查**内容**，于是这些问题一路静默漂移到很深：

- ``auto_strings``（机器抽取的占位段）排在文件最后，里面 52 条未翻译的
  条目把前面写好的手工译文整个盖掉——``tr("自动调律")`` 明明有
  ``Auto Tuning`` 却显示中文。
- 同一句中文挂在多个符号键下且译法不同时，后者静默胜出。

根因是 ``tr()`` **以中文原文为 key** 查表（见 i18n._build_translation_map：
``result[zh_value] = trans_value``），符号键只用来配对两个 yaml。所以：
同一句中文只能有一种译法，改代码里的中文就等于换 key。

棘轮项（缺失/孤儿）只减不增：把翻译补全当成一次性任务不现实，但不能再退。
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).parents[2]
_SRC = _REPO / "src" / "lvjiang"
_BASES = (_REPO / "config" / "i18n", _REPO / "config" / "i18n" / "apps" / "yysls")

#: 棘轮基线，只许下调。补翻译或删无用条目后同步调小，别放任它涨回去。
MAX_MISSING_IN_TABLE = 0     # 代码 tr() 用了、翻译表没有 → 英文界面显示中文
MAX_ORPHAN_ENTRIES = 150     # 表里有、代码已不再 tr()


def _flatten(node, prefix: str = "", out: dict | None = None) -> dict:
    out = {} if out is None else out
    for key, value in (node or {}).items():
        if key == "meta":
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            _flatten(value, path + ".", out)
        else:
            out[path] = value
    return out


def _load(base: Path, language: str) -> dict:
    return _flatten(yaml.safe_load((base / f"{language}.yaml").read_text(encoding="utf-8")))


def _tr_literals() -> set[str]:
    """AST 抽取 ``tr("字面量")``。

    **已知盲区**：``tr(_SOME_CONST)`` / ``tr(_MAP[key])`` 这类动态入参抽不出来
    （全仓约 36 处，集中在 apps/yysls/ui/tune_settings/）。它们运行时照常查表，
    机制没问题——但下面的棘轮覆盖不到，所以那些字符串缺翻译不会有人发现
    （``condition_editor._KIND_NAMES`` 就一直是未翻译状态）。要收进棘轮，得让
    这类常量本身也走一次显式的 ``tr()`` 登记，或把值内联到调用处。
    """
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args):
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


@pytest.mark.parametrize("base", _BASES, ids=lambda b: b.name)
class TestTableIntegrity:
    def test_key_sets_are_symmetric(self, base: Path):
        """结构不对齐的分支会被 _build_translation_map 静默跳过，必须对称。"""
        zh, en = _load(base, "zh_CN"), _load(base, "en_US")
        assert set(zh) == set(en), (
            f"仅 zh 有: {sorted(set(zh) - set(en))[:5]}；"
            f"仅 en 有: {sorted(set(en) - set(zh))[:5]}")

    def test_no_conflicting_duplicate_source_text(self, base: Path):
        """同一句中文只能有一种译法——查表以中文为 key，冲突时后者静默胜出。

        本次事故正是这条：auto_strings 里未翻译的条目盖掉了手工译文。
        """
        zh, en = _load(base, "zh_CN"), _load(base, "en_US")
        by_text: dict[str, list[str]] = collections.defaultdict(list)
        for key, text in zh.items():
            if isinstance(text, str):
                by_text[text].append(key)

        conflicts = {
            text: {key: en.get(key) for key in keys}
            for text, keys in by_text.items()
            if len(keys) > 1 and len({en.get(k) for k in keys}) > 1
        }
        assert not conflicts, (
            "同一句中文有多种译法，实际只有一种会生效（后者胜出）：\n"
            + "\n".join(f"  {t!r}: {m}" for t, m in sorted(conflicts.items())))

    def test_auto_strings_does_not_shadow_real_translations(self, base: Path):
        """auto_strings 是占位段；它不该盖住别处已经译好的同一句中文。"""
        zh, en = _load(base, "zh_CN"), _load(base, "en_US")
        translated = {
            text for key, text in zh.items()
            if not key.startswith("auto_strings.")
            and isinstance(text, str) and isinstance(en.get(key), str)
            and en[key] != text
        }
        shadowing = sorted(
            text for key, text in zh.items()
            if key.startswith("auto_strings.") and isinstance(text, str)
            and en.get(key) == text and text in translated
        )
        assert not shadowing, (
            f"auto_strings 里这些未翻译条目遮住了已有译文，应删除：{shadowing}")


class TestCoverageRatchet:
    """缺失与孤儿只减不增。"""

    def test_missing_translations_do_not_grow(self):
        missing = _tr_literals() - _table_texts()
        assert len(missing) <= MAX_MISSING_IN_TABLE, (
            f"未翻译字符串增加到 {len(missing)}（基线 {MAX_MISSING_IN_TABLE}）。"
            f"新增 UI 文案请同时补 config/i18n/。示例：{sorted(missing)[:5]}")

    def test_ratchet_baseline_is_tightened_when_improved(self):
        """补过翻译就要把基线调小，否则棘轮会松弛失效。"""
        missing = _tr_literals() - _table_texts()
        assert len(missing) > MAX_MISSING_IN_TABLE - 40, (
            f"实际缺失已降到 {len(missing)}，请把 MAX_MISSING_IN_TABLE 调到该值")

    def test_orphan_entries_do_not_grow(self):
        orphans = _table_texts() - _tr_literals()
        assert len(orphans) <= MAX_ORPHAN_ENTRIES, (
            f"孤儿条目增加到 {len(orphans)}（基线 {MAX_ORPHAN_ENTRIES}）。"
            f"示例：{sorted(orphans)[:5]}")


def _table_texts() -> set[str]:
    texts: set[str] = set()
    for base in _BASES:
        texts |= {v for v in _load(base, "zh_CN").values() if isinstance(v, str)}
    return texts


def test_previously_shadowed_strings_are_translated():
    """本次事故的回归点：这些字符串一度有译文却显示中文。"""
    import lvjiang.i18n as i18n

    original = i18n.current_language()
    try:
        i18n.init_i18n("en_US")
        i18n.load_app_i18n("yysls")
        for text in ("自动调律", "请至少选择一个调律规则", "日常", "脚本",
                     "失败", "主武器", "金色", "承音", "专用"):
            assert i18n.tr(text) != text, f"{text!r} 在英文界面仍显示中文"
    finally:
        i18n.init_i18n(original)
