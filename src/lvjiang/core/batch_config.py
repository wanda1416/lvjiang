"""批处理配置管理 — 多配置 + 通用 table

存储于 session.json 的 "batch" 节点，read-modify-write 模式。

数据模型：
- BatchConfig: 顶层容器，持有多个命名配置 + 当前选中配置名
- BatchConfigItem: 单个命名配置 = 名字 + 列定义 + 行数据 + 生命周期 wf
- BatchWorkflows: 批次初始化 / 条目准备 / 条目收尾 / 批次收尾

enabled 状态不在本模块管理，而是存入 session.json 的用户态
（由 batch_tab 读写），因为它是重度变化的用户态参数。
"""

from dataclasses import dataclass, field

from loguru import logger

# ─── 数据类 ──────────────────────────────────────────────


@dataclass
class BatchWorkflows:
    """批量任务生命周期 wf 槽位。"""
    batch_setup: str = ""
    prepare_item: str = ""
    finish_item: str = ""
    batch_teardown: str = ""

    def to_dict(self) -> dict:
        return {
            "batch_setup": self.batch_setup,
            "prepare_item": self.prepare_item,
            "finish_item": self.finish_item,
            "batch_teardown": self.batch_teardown,
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchWorkflows":
        return BatchWorkflows(
            batch_setup=d.get("batch_setup", ""),
            prepare_item=d.get("prepare_item", ""),
            finish_item=d.get("finish_item", ""),
            batch_teardown=d.get("batch_teardown", ""),
        )


@dataclass
class BatchConfigItem:
    """单个命名配置

    columns: 列名列表（用户自定义，如 ["account", "phone_tail", "role", "role_index"]）
    rows: 行数据列表，每行是 {col_name: value_str} 字典
    workflows: 批量任务生命周期 wf 槽位
    user_column: 指定哪一列的值作为用户名（用于 session 切换）
    """
    name: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    user_column: str = ""  # 指定哪一列的值作为用户名（用于 session 切换）
    skip_lifecycle_for_single_item: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "rows": [dict(r) for r in self.rows],
            "workflows": self.workflows.to_dict(),
            "user_column": self.user_column,
            "skip_lifecycle_for_single_item": self.skip_lifecycle_for_single_item,
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchConfigItem":
        return BatchConfigItem(
            name=d.get("name", ""),
            columns=list(d.get("columns", [])),
            rows=[dict(r) for r in d.get("rows", [])],
            workflows=BatchWorkflows.from_dict(d.get("workflows", {})),
            user_column=d.get("user_column", ""),
            skip_lifecycle_for_single_item=bool(
                d.get("skip_lifecycle_for_single_item", True)
            ),
        )


@dataclass
class BatchConfig:
    """批处理配置（多配置）

    configs: {配置名: BatchConfigItem}
    active_config: 当前选中的配置名
    script_ids: 按执行顺序排列的已勾选脚本 ID（所有配置共享）
    """
    configs: dict[str, BatchConfigItem] = field(default_factory=dict)
    active_config: str = ""
    script_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "configs": {k: v.to_dict() for k, v in self.configs.items()},
            "active_config": self.active_config,
            "script_ids": list(self.script_ids),
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchConfig":
        configs = {}
        for name, item_d in d.get("configs", {}).items():
            item = BatchConfigItem.from_dict(item_d)
            if not item.name:
                item.name = name
            configs[name] = item
        return BatchConfig(
            configs=configs,
            active_config=d.get("active_config", ""),
            script_ids=list(d.get("script_ids", [])),
        )

    def get_active(self) -> BatchConfigItem | None:
        """获取当前选中的配置"""
        if self.active_config and self.active_config in self.configs:
            return self.configs[self.active_config]
        # 降级：返回第一个配置
        if self.configs:
            first = next(iter(self.configs.values()))
            self.active_config = first.name
            return first
        return None


# ─── 持久化 ──────────────────────────────────────────────


def load_batch_config() -> BatchConfig:
    """从 session.json 读取批处理配置"""
    from .config.session import get_session_store
    node = get_session_store().get_node("batch", {})
    if not isinstance(node, dict):
        return BatchConfig()
    return BatchConfig.from_dict(node)


def save_batch_config(cfg: BatchConfig) -> None:
    """保存批处理配置到 session.json 的 batch 节点（保留 enabled_rows 用户态）"""
    from .config.session import get_session_store

    owned = set(BatchConfig().to_dict())

    def _merge(old):
        # 只覆盖 BatchConfig 自己拥有的键。整节点覆写会把同住 batch 节点的
        # 其他键（enabled_rows，以及日后任何新增键）连带抹掉。
        merged = dict(old) if isinstance(old, dict) else {}
        merged.update({k: v for k, v in cfg.to_dict().items() if k in owned})
        return merged

    get_session_store().mutate_node("batch", _merge)
    total_rows = sum(len(c.rows) for c in cfg.configs.values())
    logger.info(f"批处理配置已保存: {len(cfg.configs)} 个配置, {total_rows} 行数据")


# ─── enabled 行状态（用户态，与配置同存 batch 节点）──────────


def load_enabled_rows() -> dict[str, list[bool]]:
    """读取各配置的行勾选状态。"""
    from .config.session import get_session_store
    batch = get_session_store().get_node("batch", {})
    if not isinstance(batch, dict):
        return {}
    value = batch.get("enabled_rows", {})
    return value if isinstance(value, dict) else {}


def save_enabled_rows(enabled_rows: dict[str, list[bool]]) -> None:
    """写回行勾选状态，不触碰 batch 节点的其他键。"""
    from .config.session import get_session_store
    get_session_store().mutate_node(
        "batch",
        lambda old: {**(old if isinstance(old, dict) else {}),
                     "enabled_rows": enabled_rows},
    )


def config_enabled_flags(config: BatchConfigItem) -> list[bool]:
    """返回与 ``config.rows`` 等长的勾选状态，缺失位一律视为启用。

    ``enabled_rows`` 是按行号索引的定长 bool 数组。所有读它的地方必须共用
    这一套补齐规则——两边各写一份越界兜底，就会出现界面显示打勾、执行却把
    这行跳过去的静默偏差。
    """
    stored = load_enabled_rows().get(config.name) or []
    return [
        bool(stored[i]) if i < len(stored) else True
        for i in range(len(config.rows))
    ]
