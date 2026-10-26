"""公共批量调度器的 Qt 线程适配器。"""
# Re-export the public batch contracts used by desktop callers.
from loguru import logger as logger
from PyQt6.QtCore import QThread, pyqtSignal

from ...core.batch_scheduler import (  # noqa: F401
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_STOPPED,
    RESULT_SUCCESS,
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_STOPPED,
    ST_SUCCESS,
    BatchCheckResult,
    BatchContext,
    BatchRunSpec,
    BatchScheduler,
    BatchScript,
    BatchStageResult,
    PlannedTask,
    UnattendedInterrupt,
)


class BatchWorker(BatchScheduler, QThread):
    progress = pyqtSignal(int, str, str, str)
    selected_task_plan = pyqtSignal(int, object)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(dict)

    def __init__(self, spec, ctx, session_manager, stop_check, parent=None,
                 *, task_stop_check=None, task_stop_clear=None):
        QThread.__init__(self, parent)
        # Use Qt signals rather than assigning instance event objects.
        BatchScheduler.__init__(
            self, spec, ctx, session_manager, stop_check,
            task_stop_check=task_stop_check, task_stop_clear=task_stop_clear,
        )

    def run(self):
        BatchScheduler.run(self)
