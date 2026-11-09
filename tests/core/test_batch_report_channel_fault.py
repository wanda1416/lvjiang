"""批量报告的结尾口径：不许把"没跑起来"写成"正常完成"。

真实事故：流断后 5 次尝试全部停在「条目准备失败/跳过」，报告仍写
「（正常完成）」「脚本执行 0 次」，用户只看到批量悄悄结束。
"""

from __future__ import annotations

from lvjiang.core.batch_report import BatchReport


def _report() -> BatchReport:
    return BatchReport("刷渡尘墟", [("weekly", "周常渡尘墟速刷")], {}, total_rows=400)


def test_channel_fault_is_reported_as_abort_not_success():
    report = _report()
    report.start_batch()
    report.start_entry("用户A · 第 1 次", "钱七")
    report.record_prepare("跳过")
    report.record_channel_fault("画面流恢复失败：亮屏但 30 秒没有新画面")
    report.end_entry()
    report.end_batch()

    text = report.render()

    assert "执行通道中断，整批中止" in text
    assert "中断原因：画面流恢复失败" in text
    assert "通道中断（整批中止）" in text
    assert "正常完成" not in text


def test_zero_script_batch_is_not_reported_as_completed():
    """一个脚本都没执行（只有生命周期失败）时，不能说「正常完成」。"""
    report = _report()
    report.start_batch()
    report.start_entry("用户A · 第 1 次", "钱七")
    report.record_prepare("失败")
    report.end_entry()
    report.end_batch()

    text = report.render()

    assert "未执行任何脚本" in text
    assert "正常完成" not in text
