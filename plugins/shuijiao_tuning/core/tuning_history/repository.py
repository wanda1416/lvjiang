from lvjiang.apps.yysls.core.tuning_history.repository import (
    TuningHistoryRepository as BaseRepository,
)


class TuningHistoryRepository(BaseRepository):
    def frequency_records(self) -> list[dict]:
        """本地统计读取全部历史，不受历史首页 200 次显示上限限制。"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT e.*, r.username
                FROM tuning_equipment e
                JOIN tuning_runs r ON r.run_id=e.run_id
                ORDER BY r.started_at, e.sequence_id
            """).fetchall()
        return [dict(row) for row in rows]
