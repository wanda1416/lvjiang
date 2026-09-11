"""批量执行配置的独立持久化存储。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fasteners import InterProcessLock
from loguru import logger

from .fs_util import atomic_write_text

BATCH_DOCUMENT_TYPE = "lvjiang.batch"
BATCH_CONFIG_VERSION = 1
_WORKFLOW_PHASES = (
    "batch_setup", "prepare_item", "finish_item", "batch_teardown",
)


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
    def from_dict(data: object) -> "BatchWorkflows":
        source = data if isinstance(data, dict) else {}
        return BatchWorkflows(**{
            key: str(source.get(key, ""))
            for key in ("batch_setup", "prepare_item", "finish_item", "batch_teardown")
        })


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


@dataclass
class BatchConfigItem:
    """一个配置组：内层可见范围与外层实际执行选择。"""

    name: str = ""
    task_ids: list[str] = field(default_factory=list)
    usernames: list[str] = field(default_factory=list)
    selected_task_ids: list[str] = field(default_factory=list)
    selected_usernames: list[str] = field(default_factory=list)
    rounds: int = 1
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    workflow_params: dict[str, dict] = field(default_factory=dict)

    def normalize(self) -> None:
        from .user_config import is_valid_username

        self.task_ids = _unique_strings(self.task_ids)
        self.usernames = [
            name for name in _unique_strings(self.usernames) if is_valid_username(name)
        ]
        selected_tasks = set(_unique_strings(self.selected_task_ids))
        selected_users = set(_unique_strings(self.selected_usernames))
        self.selected_task_ids = [
            task_id for task_id in self.task_ids if task_id in selected_tasks
        ]
        self.selected_usernames = [
            name for name in self.usernames if name in selected_users
        ]
        if not isinstance(self.rounds, int) or isinstance(self.rounds, bool):
            self.rounds = 1
        self.rounds = min(999, max(1, self.rounds))
        self.workflow_params = {
            phase: dict(values)
            for phase, values in self.workflow_params.items()
            if phase in _WORKFLOW_PHASES and isinstance(values, dict)
        }

    def to_dict(self) -> dict:
        self.normalize()
        return {
            "task_ids": list(self.task_ids),
            "usernames": list(self.usernames),
            "selected_task_ids": list(self.selected_task_ids),
            "selected_usernames": list(self.selected_usernames),
            "rounds": self.rounds,
            "workflows": self.workflows.to_dict(),
            "workflow_params": self.workflow_params,
        }

    @staticmethod
    def from_dict(name: str, data: object) -> "BatchConfigItem":
        source = data if isinstance(data, dict) else {}
        item = BatchConfigItem(
            name=name,
            task_ids=_unique_strings(source.get("task_ids")),
            usernames=_unique_strings(source.get("usernames")),
            selected_task_ids=_unique_strings(source.get("selected_task_ids")),
            selected_usernames=_unique_strings(source.get("selected_usernames")),
            rounds=source.get("rounds", 1),
            workflows=BatchWorkflows.from_dict(source.get("workflows")),
            workflow_params=(dict(source.get("workflow_params", {}))
                             if isinstance(source.get("workflow_params"), dict)
                             else {}),
        )
        item.normalize()
        return item


def lifecycle_parameter_definitions(
    workflows: BatchWorkflows,
) -> dict[str, list[dict]]:
    """读取四个生命周期工作流声明的参数定义。"""
    from ..workflows.metadata import metadata_for_script_config
    from .config.resolver import get_resolver

    definitions: dict[str, list[dict]] = {}
    for phase in _WORKFLOW_PHASES:
        wf_name = getattr(workflows, phase)
        path = (get_resolver().resolve_read(f"workflows/{wf_name}")
                if wf_name else None)
        if path is None:
            definitions[phase] = []
            continue
        metadata, _warning = metadata_for_script_config(path)
        definitions[phase] = list(metadata.get("parameters") or [])
    return definitions


@dataclass
class BatchConfig:
    configs: dict[str, BatchConfigItem] = field(default_factory=dict)
    active_config: str = ""

    def to_dict(self) -> dict:
        if self.active_config not in self.configs:
            self.active_config = next(iter(self.configs), "")
        return {
            "document_type": BATCH_DOCUMENT_TYPE,
            "version": BATCH_CONFIG_VERSION,
            "active_group": self.active_config,
            "groups": {name: item.to_dict() for name, item in self.configs.items()},
        }

    @staticmethod
    def from_dict(data: object) -> "BatchConfig":
        if (
            not isinstance(data, dict)
            or data.get("document_type") != BATCH_DOCUMENT_TYPE
            or data.get("version") != BATCH_CONFIG_VERSION
        ):
            return BatchConfig()
        configs: dict[str, BatchConfigItem] = {}
        groups = data.get("groups", {})
        if isinstance(groups, dict):
            for name, raw in groups.items():
                if isinstance(name, str) and name and isinstance(raw, dict):
                    configs[name] = BatchConfigItem.from_dict(name, raw)
        active = str(data.get("active_group", ""))
        if active not in configs:
            active = next(iter(configs), "")
        return BatchConfig(configs=configs, active_config=active)

    def get_active(self) -> BatchConfigItem | None:
        return self.configs.get(self.active_config)


class BatchConfigStore:
    LOCK_TIMEOUT = 5

    def __init__(self, path: Path | None = None):
        if path is None:
            from .. import constants
            path = constants.BATCH_CONFIG_PATH
        self.path = Path(path)
        self._thread_lock = threading.RLock()
        self._file_lock = InterProcessLock(str(self.path) + ".lock")

    def _load_unlocked(self) -> BatchConfig:
        if not self.path.exists():
            return BatchConfig()
        try:
            return BatchConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"批量配置读取失败: {self.path}: {exc}")
            return BatchConfig()

    def load(self) -> BatchConfig:
        with self._thread_lock:
            return self._load_unlocked()

    def _acquire(self) -> None:
        if not self._file_lock.acquire(blocking=True, timeout=self.LOCK_TIMEOUT):
            raise TimeoutError(f"无法在 {self.LOCK_TIMEOUT}s 内获取 batch.json 写锁")

    def save(self, config: BatchConfig) -> None:
        with self._thread_lock:
            self._acquire()
            try:
                text = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
                atomic_write_text(self.path, text, prefix=".batch_")
            finally:
                self._file_lock.release()

    def mutate(self, mutator: Callable[[BatchConfig], None]) -> BatchConfig:
        with self._thread_lock:
            self._acquire()
            try:
                config = self._load_unlocked()
                mutator(config)
                text = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
                atomic_write_text(self.path, text, prefix=".batch_")
                return config
            finally:
                self._file_lock.release()


def load_batch_config() -> BatchConfig:
    return BatchConfigStore().load()


def save_batch_config(config: BatchConfig) -> None:
    BatchConfigStore().save(config)
    logger.info(f"批量配置已保存: {len(config.configs)} 个配置组")


def mutate_batch_config(mutator: Callable[[BatchConfig], None]) -> BatchConfig:
    return BatchConfigStore().mutate(mutator)


def remove_username_from_batch_configs(username: str) -> None:
    def remove(config: BatchConfig) -> None:
        for item in config.configs.values():
            item.usernames = [value for value in item.usernames if value != username]
            item.selected_usernames = [
                value for value in item.selected_usernames if value != username
            ]

    mutate_batch_config(remove)
