"""批量执行配置的独立持久化存储。"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fasteners import InterProcessLock
from loguru import logger

from .fs_util import atomic_write_text

BATCH_DOCUMENT_TYPE = "lvjiang.batch"
BATCH_CONFIG_VERSION = 2
_WORKFLOW_PHASES = (
    "batch_setup", "prepare_item", "finish_item", "batch_teardown",
    # 无人值守恢复：不在正常流程里排程，只在引擎撞到弹窗后由调度器调用。
    "recover_unattended",
)


@dataclass
class BatchWorkflows:
    batch_setup: str = ""
    prepare_item: str = ""
    finish_item: str = ""
    batch_teardown: str = ""
    recover_unattended: str = ""

    def to_dict(self) -> dict:
        return {
            "batch_setup": self.batch_setup,
            "prepare_item": self.prepare_item,
            "finish_item": self.finish_item,
            "batch_teardown": self.batch_teardown,
            "recover_unattended": self.recover_unattended,
        }

    @staticmethod
    def from_dict(data: object) -> "BatchWorkflows":
        source = data if isinstance(data, dict) else {}
        return BatchWorkflows(**{
            key: str(source.get(key, ""))
            for key in _WORKFLOW_PHASES
        })


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


@dataclass
class BatchConfigItem:
    """一个配置组的**定义**：可见范围、初始顺序、默认勾选与生命周期结构。

    这里只放"配置组是什么"。"本次要跑哪些、跑几轮、有没有人看守"属于运行草稿
    （`core/batch_run.py`），存在 session 里，由主页面维护——定义层永远不被
    主页面的一次临时选择改写。
    """

    #: 稳定 ID。草稿、执行历史都按它关联；名称只用于展示，可随时改。
    id: str = ""
    name: str = ""
    #: 可见任务及初始顺序（配置组定义的全部候选）
    task_ids: list[str] = field(default_factory=list)
    #: 可见用户及初始顺序
    usernames: list[str] = field(default_factory=list)
    #: 默认勾选：配置组首次使用、或用户点「恢复默认」时勾上哪些
    default_task_ids: list[str] = field(default_factory=list)
    default_usernames: list[str] = field(default_factory=list)
    #: 属性单元的默认勾选，按属性键分开存
    default_units: dict[str, list[str]] = field(default_factory=dict)
    #: 调度单元。它决定主页面候选列表的内容、生命周期契约和准备 wf 的合法性，
    #: 属于配置组定义，不是单次选择。
    execution_unit_key: str = "user"
    #: 右键「按 Profile 排序」用哪个键、什么方向。定义的是排序**能力**，
    #: 排完的实际顺序属于运行草稿。
    profile_sort_key: str = ""
    profile_sort_direction: str = "asc"
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    workflow_params: dict[str, dict] = field(default_factory=dict)
    skip_lifecycle_for_single_item: bool = True

    def __post_init__(self) -> None:
        # ID 在构造时就得有：调用方往往先 `configs[item.id] = item` 再保存，
        # 留到 normalize 才生成会让那一步拿到空字符串当 key。
        if not isinstance(self.id, str) or not self.id:
            self.id = uuid4().hex

    def normalize(self) -> None:
        from .user_config import is_valid_username

        if not isinstance(self.id, str) or not self.id:
            self.id = uuid4().hex
        self.task_ids = _unique_strings(self.task_ids)
        self.usernames = [
            name for name in _unique_strings(self.usernames) if is_valid_username(name)
        ]
        visible_tasks = set(self.task_ids)
        visible_users = set(self.usernames)
        # 默认勾选必须是可见项的子集：可见范围缩小后，留着失效的默认值只会在
        # 下次「恢复默认」时勾出已经不存在的条目。
        self.default_task_ids = [
            task_id for task_id in _unique_strings(self.default_task_ids)
            if task_id in visible_tasks
        ]
        self.default_usernames = [
            name for name in _unique_strings(self.default_usernames)
            if name in visible_users
        ]
        value = self.default_units
        self.default_units = {
            key: _unique_strings(items)
            for key, items in value.items()
            if isinstance(key, str) and key and isinstance(items, list)
        } if isinstance(value, dict) else {}
        if not isinstance(self.execution_unit_key, str) or not self.execution_unit_key:
            self.execution_unit_key = "user"
        if not isinstance(self.profile_sort_key, str):
            self.profile_sort_key = ""
        if self.profile_sort_direction not in ("asc", "desc"):
            self.profile_sort_direction = "asc"
        self.workflow_params = {
            phase: dict(values)
            for phase, values in self.workflow_params.items()
            if phase in _WORKFLOW_PHASES and isinstance(values, dict)
        }
        if not isinstance(self.skip_lifecycle_for_single_item, bool):
            self.skip_lifecycle_for_single_item = True

    def to_dict(self) -> dict:
        self.normalize()
        return {
            "id": self.id,
            "name": self.name,
            "task_ids": list(self.task_ids),
            "usernames": list(self.usernames),
            "default_task_ids": list(self.default_task_ids),
            "default_usernames": list(self.default_usernames),
            "default_units": {
                key: list(value) for key, value in self.default_units.items()
            },
            "execution_unit_key": self.execution_unit_key,
            "profile_sort_key": self.profile_sort_key,
            "profile_sort_direction": self.profile_sort_direction,
            "workflows": self.workflows.to_dict(),
            "workflow_params": self.workflow_params,
            "skip_lifecycle_for_single_item": self.skip_lifecycle_for_single_item,
        }

    @staticmethod
    def from_dict(name: str, data: object) -> "BatchConfigItem":
        """读取一个配置组。

        同时吃下 v1 的字段名：`selected_*` 当作默认勾选读进来（它在旧版本里
        既是上次运行的勾选、又是下次打开的初始值，拆层后只保留后一半语义），
        `rounds` / `unattended` 属于运行草稿，不再进定义层。写出去只有新格式，
        所以这是一次性的单向读取，不是常驻兼容层。
        """
        source = data if isinstance(data, dict) else {}
        raw_workflows = source.get("workflows")
        workflow_source = (
            raw_workflows if isinstance(raw_workflows, dict) else {}
        )
        workflow_params = (
            dict(source.get("workflow_params", {}))
            if isinstance(source.get("workflow_params"), dict) else {}
        )
        default_tasks = source.get("default_task_ids")
        if default_tasks is None:
            default_tasks = source.get("selected_task_ids")
        default_users = source.get("default_usernames")
        if default_users is None:
            default_users = source.get("selected_usernames")
        default_units = source.get("default_units")
        if default_units is None:
            default_units = source.get("selected_units")
        item = BatchConfigItem(
            id=str(source.get("id") or "") or uuid4().hex,
            name=name,
            task_ids=_unique_strings(source.get("task_ids")),
            usernames=_unique_strings(source.get("usernames")),
            default_task_ids=_unique_strings(default_tasks),
            default_usernames=_unique_strings(default_users),
            default_units=default_units if isinstance(default_units, dict) else {},
            execution_unit_key=source.get("execution_unit_key", "user"),
            profile_sort_key=source.get("profile_sort_key", ""),
            profile_sort_direction=source.get("profile_sort_direction", "asc"),
            workflows=BatchWorkflows.from_dict(workflow_source),
            workflow_params=workflow_params,
            skip_lifecycle_for_single_item=(
                source.get("skip_lifecycle_for_single_item", True)
                if isinstance(
                    source.get("skip_lifecycle_for_single_item", True), bool)
                else True
            ),
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


def declares_unit_prepare(wf_name: str) -> bool:
    """条目准备 wf 是否声明了「为属性单元选定用户」这条协议。

    属性单元的一个单元值可能对应多名用户，准备 wf 必须回传本轮选中的
    ``username``；不回传时批量层只能在跑起来之后才发现，且每个单元都会以同一个
    协议错误被丢弃。作者用 ``#% batch_unit_prepare: true`` 显式声明，批量层据此
    在开始前拦截，而不是靠猜 wf 内容或写死文件名——用户自写的准备 wf 同样可以声明。
    """
    from ..workflows.metadata import metadata_for_script_config
    from .config.resolver import get_resolver

    if not wf_name:
        return False
    path = get_resolver().resolve_read(f"workflows/{wf_name}")
    if path is None:
        return False
    metadata, _warning = metadata_for_script_config(path)
    return bool(metadata.get("batch_unit_prepare", False))


@dataclass
class BatchConfig:
    """全部配置组。按**稳定 ID** 索引，名称只用于展示。

    当前活动组属于主页面状态，存在 session 的 `ui_state.batch.active_group_id`，
    不在这里——"编辑哪个组"和"主页面默认跑哪个组"是两件事，放一起就会出现
    「在配置窗口切一下编辑对象，主页面默认组跟着变」这类越界。
    """

    configs: dict[str, BatchConfigItem] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 不变量：按稳定 ID 索引。调用方按名称拼出来的字典在这里自愈——
        # 否则会出现"键是名字、值的 id 是另一个"的半成品，草稿和历史的关联
        # 就会挂在一个随时会变的名字上。
        if any(key != item.id for key, item in self.configs.items()):
            self.configs = {item.id: item for item in self.configs.values()}

    def to_dict(self) -> dict:
        return {
            "document_type": BATCH_DOCUMENT_TYPE,
            "version": BATCH_CONFIG_VERSION,
            # 以条目自己的 ID 为 key：外层字典的 key 万一和它不一致，
            # 落盘后再读回来就会丢掉这个组。
            "groups": {item.id: item.to_dict() for item in self.configs.values()},
        }

    @staticmethod
    def from_dict(data: object) -> "BatchConfig":
        if (
            not isinstance(data, dict)
            or data.get("document_type") != BATCH_DOCUMENT_TYPE
        ):
            return BatchConfig()
        version = data.get("version")
        if version not in (1, BATCH_CONFIG_VERSION):
            return BatchConfig()
        configs: dict[str, BatchConfigItem] = {}
        groups = data.get("groups", {})
        if isinstance(groups, dict):
            for key, raw in groups.items():
                if not isinstance(key, str) or not key or not isinstance(raw, dict):
                    continue
                # v1 的 key 是组名；v2 的 key 是稳定 ID，名称在组内部。
                name = str(raw.get("name") or "") if version != 1 else key
                item = BatchConfigItem.from_dict(name or key, raw)
                configs[item.id] = item
        return BatchConfig(configs=configs)

    # ─── 按名称查找（界面展示用；内部一律用 ID） ───

    def names(self) -> list[str]:
        return [item.name for item in self.configs.values()]

    def by_name(self, name: str) -> BatchConfigItem | None:
        for item in self.configs.values():
            if item.name == name:
                return item
        return None

    def get(self, group_id: str) -> BatchConfigItem | None:
        return self.configs.get(group_id)

    def first_id(self) -> str:
        return next(iter(self.configs), "")

    def resolve_id(self, group_id: str) -> str:
        """给定 ID 不存在时回退到第一个组，供主页面载入时自愈。"""
        return group_id if group_id in self.configs else self.first_id()

    def add(self, item: BatchConfigItem) -> BatchConfigItem:
        item.normalize()
        self.configs[item.id] = item
        return item


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
        return BatchConfig.from_dict(self._read_document_unlocked())

    def _read_document_unlocked(self) -> object:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"批量配置读取失败: {self.path}: {exc}")
            return {}

    def load(self) -> BatchConfig:
        with self._thread_lock:
            document = self._read_document_unlocked()
            if not (
                isinstance(document, dict)
                and document.get("document_type") == BATCH_DOCUMENT_TYPE
                and document.get("version") == 1
            ):
                return BatchConfig.from_dict(document)

            # v1 没有稳定 ID。只在内存里补 uuid 会导致每次 load 都得到一组
            # 新 ID，主页面下拉框、活动组与运行草稿立即失去关联。迁移必须在
            # 文件锁内重读最新磁盘并一次性写成 v2；另一个进程若已先迁移，
            # 本进程直接使用它写出的结果。
            self._acquire()
            try:
                latest = self._read_document_unlocked()
                config = BatchConfig.from_dict(latest)
                if (
                    isinstance(latest, dict)
                    and latest.get("document_type") == BATCH_DOCUMENT_TYPE
                    and latest.get("version") == 1
                ):
                    text = json.dumps(
                        config.to_dict(), ensure_ascii=False, indent=2)
                    atomic_write_text(self.path, text, prefix=".batch_")
                    logger.info("批量配置已从 v1 一次性迁移到 v2")
                return config
            finally:
                self._file_lock.release()

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
            item.default_usernames = [
                value for value in item.default_usernames if value != username
            ]

    mutate_batch_config(remove)
