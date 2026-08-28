"""批量报告的行级统计。

状态值全部是 ``tr()`` 过的 ``ST_*`` 常量，统计侧一旦拿裸中文字面量去比，
非中文界面下就永远比不中——条目收尾失败会被算成「全部成功」。
所以这里必须在非 zh 语言下断言，zh 下裸字面量恰好等于译文，测不出问题。
"""

from __future__ import annotations

import pytest

from lvjiang import i18n
from lvjiang.ui.batch.batch_report import BatchReport

_EN = {"失败": "Failed", "成功": "Success"}


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setattr(i18n, "_current_language", "en_US")
    monkeypatch.setattr(i18n, "_translations", dict(_EN))
    assert i18n.tr("失败") == "Failed"
    return _EN


def _render(finish_status: str, script_status: str) -> str:
    report = BatchReport(
        config_name="demo",
        scripts=[("a", "脚本A")],
        workflows={"finish_item": "finish.wf"},
        total_rows=1,
    )
    report.start_batch()
    report.start_entry("acct0", "acct0")
    report.record_prepare(i18n.tr("成功"))
    report.start_script("a", "脚本A")
    report.end_script(script_status)
    report.record_finish(finish_status)
    report.end_entry()
    report.end_batch(stopped=False)
    return report.render()


def test_finish_failure_is_not_counted_as_full_success(english):
    text = _render(finish_status="Failed", script_status="Success")

    assert "- 行执行总计：1 行" in text
    # 条目收尾失败 → 不能算全部成功。
    assert "全部成功：0" in text


def test_all_success_entry_is_still_counted(english):
    text = _render(finish_status="Success", script_status="Success")

    assert "全部成功：1" in text
