"""调律历史的稳定领域模型。

这里不依赖 Qt，也不依赖自动调律实现。实时 UI、历史详情、Markdown 与
匿名统计都只能从这些结构投影各自所需的数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field

RESULT_RECYCLED = "recycled"
RESULT_SKIPPED = "skipped"
RESULT_TUNED = "tuned"
RESULT_TUNED_RECYCLED = "tuned_recycled"
RESULT_RESET = "reset"

RESET_COMPLETED = "completed"
RESET_COOLDOWN = "cooldown"
RESET_EXHAUSTED = "exhausted"
RESET_EXHAUSTED_RECYCLED = "exhausted_recycled"
RESET_MATERIAL_SHORTAGE = "material_shortage"
RESET_FAILED = "failed"
# 异常态：重置按钮上的剩余次数没读出明确数字。既不是"还有次数"也不是
# "已用尽"，不能参与次数耗尽转处置，只能跳过并如实记录。
RESET_COUNT_UNREADABLE = "count_unreadable"

#: 识别类异常：不是"重置的某种结果"，而是这一步没看清楚。卡片单独标红，
#: 历史列表按运行汇总。新增异常态只需要加进这个集合。
RESET_ANOMALIES = frozenset({RESET_COUNT_UNREADABLE})


@dataclass(frozen=True)
class TuningEquipmentResult:
    """一件装备在一次自动调律运行中的最终快照。"""

    equipment_id: int
    slot_key: str
    name: str
    type: str
    level: int | float | None
    quality: str
    final_affixes: tuple[dict, ...]
    final_rating: str
    rounds: int
    result: str
    reason: str
    reset_outcome: str = ""
    initial_affixes: tuple[dict, ...] = ()
    raw_status: str = ""
    scanned_at: str = ""
    tuning_started_at: str = ""
    finished_at: str = ""
    round_details: tuple[dict, ...] = ()
    tuning_mode: str = ""
    telemetry_stop_reason: str = ""
    telemetry_final_rating: str = ""
    resets: int = 0

    @property
    def entered_tuning(self) -> bool:
        return bool(self.tuning_started_at)


@dataclass(frozen=True)
class TuningRunSummary:
    """历史列表使用的一次运行摘要。"""

    run_id: str
    started_at: str
    finished_at: str
    username: str
    status: str
    stop_reason: str
    selected_slots: tuple[str, ...] = ()
    rule_snapshot: tuple[dict, ...] = ()
    total_equipment: int = 0
    tuned_count: int = 0
    recycled_count: int = 0
    skipped_count: int = 0
    reset_count: int = 0
    total_rounds: int = 0
    markdown_path: str = ""
    config_snapshot: dict = field(default_factory=dict)
    # 由仓储读取时按装备行实时聚合，不落库：run 崩在半路时 finish_run 从没
    # 跑过，落库的计数会全是 0，而恰恰是那种运行最需要看到异常。
    anomaly_count: int = 0
