"""调律历史回传声明；不携带装备仓库，也不在 PC 重建遥测发送队列。"""
from .....core.history_transfer import HistoryChannel, HistoryTable
from .repository import TuningHistoryRepository


def history_channels() -> tuple[HistoryChannel, ...]:
    finished = ("status != 'running' AND finished_at != '' AND "
                "((target_id='ondevice' AND target_kind='android') OR "
                "(target_id='' AND markdown_path LIKE :root_prefix))")
    return (HistoryChannel("yysls.tuning", "调律历史", "tuning_history.db", TuningHistoryRepository, (
        HistoryTable("tuning_runs", ("run_id",), finished, ("markdown_path",)),
        HistoryTable("tuning_equipment", ("run_id", "sequence_id"),
                     f"run_id IN (SELECT run_id FROM tuning_runs WHERE {finished})", omitted=("id",)),
    ), unattributed=("SELECT COUNT(*) FROM tuning_runs WHERE status != 'running' AND finished_at != '' "
                     "AND target_id='' AND markdown_path NOT LIKE :root_prefix")),)
