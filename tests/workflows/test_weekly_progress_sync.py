from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[2]
_WORKFLOWS = _ROOT / "config" / "system" / "workflows"


def test_weekly_progress_uses_one_shared_parser_and_sync_path():
    common = (_WORKFLOWS / "subcall" / "game_profile.wf").read_text(
        encoding="utf-8"
    )
    daily = (_WORKFLOWS / "daily_jianghu.wf").read_text(encoding="utf-8")
    purchase = (_WORKFLOWS / "purchase_bugan.wf").read_text(encoding="utf-8")
    xinfa = (_WORKFLOWS / "purchase_xinfa.wf").read_text(encoding="utf-8")
    wallet = (_WORKFLOWS / "scan_wallet.wf").read_text(encoding="utf-8")
    huaruizhi = (_WORKFLOWS / "weekly_huaruizhi.wf").read_text(encoding="utf-8")

    assert "def sync_weekly_progress(" in common
    assert "extract_progress($raw)" in common
    assert "profile_observe($key, $current)" in common
    assert "def sync_weekly_remaining(" in common
    assert "$consumed = $expected_total - $progress.current" in common
    assert "profile_observe($key, $consumed)" in common
    assert "split($raw" not in common
    assert '$raw contains "|"' not in common

    assert "sync_weekly_progress($result.haoling_of_week" in daily
    assert "sync_weekly_progress($jindu_str.bugan_jindu" in purchase
    assert "sync_weekly_progress($jindu_str.bugan_jindu" in wallet
    assert "parse_bugan_jindu" not in purchase
    assert "parse_bugan_jindu" not in wallet
    assert "sync_weekly_progress($progress.huaruizhi_of_week" in huaruizhi
    assert xinfa.count("sync_weekly_remaining(") == 2
    registry = (_WORKFLOWS / "subcall" / "profile_registry.wf").read_text(
        encoding="utf-8"
    )
    assert 'declare_profiles(["xinfa_of_week"])' in xinfa
    assert '"xinfa_of_week": {"model": "quota"' in registry
    assert '"cap": 600' in registry
