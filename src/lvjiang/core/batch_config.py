"""批量执行配置：用户名顺序、生命周期工作流和任务脚本。"""
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class BatchWorkflows:
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
    def from_dict(data: dict) -> "BatchWorkflows":
        return BatchWorkflows(**{
            key: str(data.get(key, ""))
            for key in ("batch_setup", "prepare_item", "finish_item", "batch_teardown")
        })


@dataclass
class BatchConfigItem:
    """一个命名批量方案。用户名是唯一条目数据。"""

    name: str = ""
    usernames: list[str] = field(default_factory=list)
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "usernames": list(dict.fromkeys(self.usernames)),
            "workflows": self.workflows.to_dict(),
        }

    @staticmethod
    def from_dict(data: dict) -> "BatchConfigItem":
        from .user_config import is_valid_username

        usernames = data.get("usernames", [])
        return BatchConfigItem(
            name=str(data.get("name", "")),
            usernames=list(dict.fromkeys(
                str(value) for value in usernames
                if isinstance(value, str) and is_valid_username(value)
            )),
            workflows=BatchWorkflows.from_dict(data.get("workflows", {})),
        )


@dataclass
class BatchConfig:
    configs: dict[str, BatchConfigItem] = field(default_factory=dict)
    active_config: str = ""
    script_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "configs": {name: item.to_dict() for name, item in self.configs.items()},
            "active_config": self.active_config,
            "script_ids": list(self.script_ids),
        }

    @staticmethod
    def from_dict(data: dict) -> "BatchConfig":
        configs = {}
        raw_configs = data.get("configs", {})
        if isinstance(raw_configs, dict):
            for name, raw in raw_configs.items():
                if not isinstance(raw, dict):
                    continue
                item = BatchConfigItem.from_dict(raw)
                item.name = item.name or str(name)
                configs[str(name)] = item
        return BatchConfig(
            configs=configs,
            active_config=str(data.get("active_config", "")),
            script_ids=[str(value) for value in data.get("script_ids", [])],
        )

    def get_active(self) -> BatchConfigItem | None:
        if self.active_config in self.configs:
            return self.configs[self.active_config]
        if self.configs:
            item = next(iter(self.configs.values()))
            self.active_config = item.name
            return item
        return None


def load_batch_config() -> BatchConfig:
    from .config.session import get_session_store

    node = get_session_store().get_node("batch", {})
    return BatchConfig.from_dict(node) if isinstance(node, dict) else BatchConfig()


def save_batch_config(config: BatchConfig) -> None:
    from .config.session import get_session_store

    get_session_store().mutate_node("batch", lambda _: config.to_dict())
    total = sum(len(item.usernames) for item in config.configs.values())
    logger.info(f"批处理配置已保存: {len(config.configs)} 个配置, {total} 个用户")


def remove_username_from_batch_configs(username: str) -> None:
    """从所有批量配置中移除已删除用户，保留其他批量节点内容。"""
    from .config.session import get_session_store

    def remove(raw):
        if not isinstance(raw, dict):
            return raw
        config = BatchConfig.from_dict(raw)
        changed = False
        for item in config.configs.values():
            filtered = [value for value in item.usernames if value != username]
            if filtered != item.usernames:
                item.usernames = filtered
                changed = True
        return config.to_dict() if changed else raw

    get_session_store().mutate_node("batch", remove)
