"""wf 顶层返回值的通用失败协议：只认结构，不认任务。"""
import pytest

from lvjiang.workflows.result_protocol import RESULT_FAILED, return_failure_message


@pytest.mark.parametrize("value", [
    None, 0, 0.0, 1, 8, 2.5, True, False, "", "-1", [], {},
    {"status": "success", "message": "x"}, {"status": "skipped"}, {"error": "x"},
    {"message": "没有 status 不算失败"},
])
def test_non_failure_values(value):
    assert return_failure_message(value) is None


def test_failed_status_dict_uses_its_message():
    assert return_failure_message(
        {"status": RESULT_FAILED, "message": "任务自己的失败文案"}) == "任务自己的失败文案"


@pytest.mark.parametrize("value", [{"status": "failed"}, {"status": "failed", "message": ""}])
def test_failed_status_without_message_gets_generic_text(value):
    assert return_failure_message(value) == "脚本返回失败"


@pytest.mark.parametrize("value, shown", [(-1, "-1"), (-2, "-2"), (-1.0, "-1"), (-0.5, "-0.5")])
def test_negative_numbers_fail(value, shown):
    message = return_failure_message(value)
    assert message is not None and shown in message
