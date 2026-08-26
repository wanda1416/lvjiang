"""调律遥测事件分析引擎（从 ``scripts/analyze_telemetry_rolls.py`` 拆出）。

纯标准库，不依赖 numpy/pandas/scipy——见 ``analyze_telemetry_rolls.py`` 顶部
docstring 的理由。拆分目的是给 ``ops/stats-client`` 提供可 import 的分析逻辑，
而不必 subprocess 调用 CLI；CLI 本身的行为不因这次拆分而改变，见
``scripts/analyze_telemetry_rolls.py`` 现在只是一个薄壳。

模块划分：

- ``loader``   — 三种输入形态（NDJSON / D1 导出 JSON / 裸 JSON 数组）的加载与展开
- ``metrics``  — 统计工具（Wilson 区间、分位数）+ 词条/部位/材料等展示用常量
- ``sections`` — 报告的每一节，输入事件列表、输出 Markdown 行
- ``render_md``— 组装完整报告（相当于原来 CLI ``main()`` 里构建报告的那部分）
"""
from .loader import SCHEMA_NAME, flatten_rolls, load_events, subsample_per_install
from .metrics import MIN_CELL_N, quantiles, roll_bucket, wilson
from .render_md import build_report
from .slots import (
                    SlotItem,
                    SlotStat,
                    conditional_slot_distribution,
                    observed_slots,
                    parse_slot_range,
                    reconstruct_all,
                    reconstruct_final_state,
                    slot_distribution,
                    slot_range_distribution,
)

__all__ = [
    "SCHEMA_NAME", "load_events", "flatten_rolls", "subsample_per_install",
    "MIN_CELL_N", "wilson", "quantiles", "roll_bucket", "build_report",
    "SlotItem", "SlotStat", "reconstruct_final_state", "reconstruct_all",
    "slot_distribution", "slot_range_distribution",
    "conditional_slot_distribution", "observed_slots", "parse_slot_range",
]
