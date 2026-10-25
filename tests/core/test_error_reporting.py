"""统一记账入口 `log_failure` 的契约。

Profile 的「脚本拒绝」和「写入冲突」都是预期结果：消息说明原因，日志只记一行
WARNING，不打印堆栈。意外异常仍必须带堆栈——否则真实缺陷会失去唯一线索。
"""

from loguru import logger as loguru_logger

from lvjiang.core.profile.service import ProfileOperationRefused, ProfileWriteConflict
from lvjiang.core.profile.transactions import ProfileWriteRejected
from lvjiang.errors import log_failure


def _capture(records: list[str]):
    return loguru_logger.add(
        lambda message: records.append(str(message).strip()),
        level="DEBUG", format="{level} | {message}",
    )


def test_profile_write_failures_log_one_warning_line_without_traceback():
    records: list[str] = []
    sink_id = _capture(records)
    try:
        for error in (
            ProfileWriteRejected("承音石获取超过来源上限：武墓证道"),
            ProfileWriteConflict("数据已发生变化，请刷新后重试"),
            ProfileOperationRefused("只能撤销该用户此数据项的最新记录"),
        ):
            records.clear()
            log_failure("回写失败", error)
            logged = [line for line in records if line.startswith("WARNING | ")]
            assert len(logged) == 1
            assert str(error) in logged[0]
            assert not any("Traceback" in line for line in records)
    finally:
        loguru_logger.remove(sink_id)


def test_unexpected_failure_still_carries_a_traceback():
    records: list[str] = []
    sink_id = _capture(records)
    try:
        try:
            raise RuntimeError("boom")
        except RuntimeError as error:
            log_failure("回写失败", error)
    finally:
        loguru_logger.remove(sink_id)

    logged = [line for line in records if line.startswith("ERROR | ")]
    assert len(logged) == 1
    assert "boom" in logged[0] and "Traceback" in logged[0]
