from pathlib import Path


def test_account_switch_requires_nonempty_tail():
    source = (
        Path(__file__).parents[2]
        / "config/system/workflows/batch/prepare_item.wf"
    ).read_text(encoding="utf-8")
    switch_block = source.partition(
        "if not $batch_state.account equals $account"
    )[2].partition("end\n\nlog \"切换角色")[0]
    assert "if not $tail" in switch_block
    assert "用户未配置账号尾号" in switch_block
