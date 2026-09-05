"""调律工作流运行上下文 — UI 注入字段的显式契约

此前 tuning_tab 的 configure 回调直接往工作流实例上塞下划线属性
（_selected_slots/_skip_tuning/...），读取端只能 getattr 防御，契约
不在类型系统里，重命名即静默失效。收口为 dataclass 后，注入端
（tuning_tab/测试）与读取端（auto_tuning）共享同一份
字段定义，类型检查与 IDE 重命名均可覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.tuning_rules import TuningGroup


@dataclass
class TuningRunContext:
    """自动/单件调律的运行期注入配置

    由 UI（tuning_tab 的 configure 回调）或测试在启动前整体赋给
    workflow.run_ctx；工作流内部经 self.ctx 惰性访问，未注入时
    各字段取默认值（等价此前 getattr 的兜底行为）。
    """

    selected_slots: list[str] | None = None   # 调律部位（None/空=全部部位）
    rule_judges: list = field(default_factory=list)  # UI 构建的判定器（暂未消费，仅记录）
    judge_configs: dict | None = None         # 潜力判定配置（None=未注入，回退插件 session）
    judge_rule_keys: list[str] | None = None  # 参与判定的规则 key 顺序
    skip_tuning: bool = False                 # 测试开关：跳过实际调律，仅模拟进出调律页
    pc_background_scroll: bool = False        # PC 后台模式：滚轮替代背包拖拽
    use_stone_cache: bool = True              # 使用律准石消耗/返还账本
    initial_stone_min_count: int | None = None  # 首次 OCR 额外校验（大律准石）
    doc_dir: Path | None = None               # 说明文档输出目录覆盖（供测试）
    history_db_path: Path | None = None        # 历史数据库路径覆盖（供测试）
    scroll_strategy: str = ""                 # 背包遍历策略 key（空=读 session/默认）
    skip_start: tuple[int, int] | None = None    # 初始跳过 (row, col)；None=不跳过
    target_cell: tuple[int, int] | None = None   # 指定调律 (row, col)；None=不指定
    base_group: TuningGroup | None = None        # 基础规则组（None=回退 session）
    min_level: int | None = None                 # 最低等级覆盖（None=跟随基础规则 scan.min_level）


class TuningContextMixin:
    """为调律工作流提供 run_ctx 的惰性访问入口"""

    run_ctx: TuningRunContext | None = None

    @property
    def ctx(self) -> TuningRunContext:
        """运行上下文；未注入时惰性创建全默认实例"""
        if self.run_ctx is None:
            self.run_ctx = TuningRunContext()
        return self.run_ctx
