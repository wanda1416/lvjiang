"""批处理配置管理 — 多配置 + 通用 table

存储于 session.json 的 "batch" 节点，read-modify-write 模式。

数据模型：
- BatchConfig: 顶层容器，持有多个命名配置 + 当前选中配置名
- BatchConfigItem: 单个命名配置 = 名字 + 列定义 + 行数据 + 三个 wf 槽位
- BatchWorkflows: 预处理 / 切换 / 后处理 三个 wf 路径

enabled 状态不在本模块管理，而是存入 session.json 的用户态
（由 batch_tab 读写），因为它是重度变化的用户态参数。
"""

import json
from dataclasses import dataclass, field

from loguru import logger

from ..constants import SESSION_CONFIG_DIR, SESSION_PATH

# ─── 数据类 ──────────────────────────────────────────────


@dataclass
class BatchWorkflows:
    """三个 wf 槽位"""
    preprocess: str = ""   # 预处理 wf（首次登录 / 账号变化时调用）
    switch: str = ""       # 切换 wf（同账号切换角色时调用）
    postprocess: str = ""  # 后处理 wf（可选，当前未使用）

    def to_dict(self) -> dict:
        return {
            "preprocess": self.preprocess,
            "switch": self.switch,
            "postprocess": self.postprocess,
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchWorkflows":
        return BatchWorkflows(
            preprocess=d.get("preprocess", ""),
            switch=d.get("switch", ""),
            postprocess=d.get("postprocess", ""),
        )


@dataclass
class BatchConfigItem:
    """单个命名配置

    columns: 列名列表（用户自定义，如 ["account", "phone_tail", "role", "role_index"]）
    rows: 行数据列表，每行是 {col_name: value_str} 字典
    workflows: 三个 wf 槽位
    user_column: 指定哪一列的值作为用户名（用于 session 切换）
    """
    name: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    user_column: str = ""  # 指定哪一列的值作为用户名（用于 session 切换）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "rows": [dict(r) for r in self.rows],
            "workflows": self.workflows.to_dict(),
            "user_column": self.user_column,
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchConfigItem":
        return BatchConfigItem(
            name=d.get("name", ""),
            columns=list(d.get("columns", [])),
            rows=[dict(r) for r in d.get("rows", [])],
            workflows=BatchWorkflows.from_dict(d.get("workflows", {})),
            user_column=d.get("user_column", ""),
        )


@dataclass
class BatchConfig:
    """批处理配置（多配置）

    configs: {配置名: BatchConfigItem}
    active_config: 当前选中的配置名
    script_ids: 勾选的脚本 ID 列表（所有配置共享）
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
    if not SESSION_PATH.exists():
        return BatchConfig()
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        return BatchConfig.from_dict(data.get("batch", {}))
    except Exception as e:
        logger.error(f"加载批处理配置失败: {e}")
        return BatchConfig()


def save_batch_config(cfg: BatchConfig) -> None:
    """保存批处理配置到 session.json（read-modify-write）"""
    data: dict = {}
    if SESSION_PATH.exists():
        try:
            data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 保留 enabled_rows（用户态，由 batch_tab 管理）
    enabled_rows = data.get("batch", {}).get("enabled_rows")
    data["batch"] = cfg.to_dict()
    if enabled_rows is not None:
        data["batch"]["enabled_rows"] = enabled_rows
    SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    total_rows = sum(len(c.rows) for c in cfg.configs.values())
    logger.info(f"批处理配置已保存: {len(cfg.configs)} 个配置, {total_rows} 行数据")


def get_active_config() -> BatchConfigItem | None:
    """获取当前选中的配置"""
    return load_batch_config().get_active()
