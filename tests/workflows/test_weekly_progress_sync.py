from __future__ import annotations

from pathlib import Path

from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar.ast_nodes import Eval

_ROOT = Path(__file__).parents[2]
_WORKFLOWS = _ROOT / "config" / "system" / "workflows"


def _registry_entry(source: str, key: str) -> dict:
    """按 key 取 ``$registry`` 里的条目定义。

    断言只关心"注册表里这个 key 是什么模型、上限多少"，与是否折行无关，
    因此从 AST 取而不是匹配源码字符串。
    """
    program = parse_text(source)
    # $registry 定义在 declare_profiles 过程内部，不在顶层 body。
    bodies = [program.body] + [proc.body for proc in program.procs.values()]
    for body in bodies:
        for stmt in body:
            if isinstance(stmt, Eval) and stmt.target == "registry":
                return stmt.func_args[0][key]
    raise AssertionError("未找到 $registry 定义")


def _literal(node):
    return getattr(node, "value", node)



def test_weekly_progress_uses_one_shared_parser_and_sync_path():
    common = (_WORKFLOWS / "subcall" / "game_profile.wf").read_text(
        encoding="utf-8"
    )
    daily = (_WORKFLOWS / "daily_jianghu.wf").read_text(encoding="utf-8")
    purchase = (_WORKFLOWS / "purchase_bugan.wf").read_text(encoding="utf-8")
    xinfa = (_WORKFLOWS / "purchase_xinfa.wf").read_text(encoding="utf-8")
    wallet = (_WORKFLOWS / "scan_wallet.wf").read_text(encoding="utf-8")

    assert "def sync_weekly_progress(" in common
    assert "extract_progress($raw)" in common
    assert "profile_observe($key, $current, $source)" in common
    assert "def sync_weekly_remaining(" in common
    assert "$consumed = $expected_total - $progress.current" in common
    assert "profile_observe($key, $consumed, $source)" in common
    assert "split($raw" not in common
    assert '$raw contains "|"' not in common

    assert "sync_weekly_progress($result.haoling_of_week" in daily
    assert '"当周获取号令", "江湖号令")' in daily
    assert "sync_weekly_progress($jindu_str.bugan_jindu" in purchase
    assert '"本周不肝进度", "日常活动")' in purchase
    assert "sync_weekly_progress($jindu_str.bugan_jindu" in wallet
    assert "parse_bugan_jindu" not in purchase
    assert "parse_bugan_jindu" not in wallet
    assert xinfa.count("sync_weekly_remaining(") == 2
    assert xinfa.count('"本周心法心得购买", "日常")') == 2
    registry = (_WORKFLOWS / "subcall" / "profile_registry.wf").read_text(
        encoding="utf-8"
    )
    assert 'declare_profiles(["xinfa_of_week"])' in xinfa
    xinfa_entry = _registry_entry(registry, "xinfa_of_week")
    assert _literal(xinfa_entry["model"]) == "quota"
    assert _literal(xinfa_entry["definition"]["cap"]) == 600
