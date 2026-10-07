"""批量的运行态：本次执行草稿。

批量有三层状态，边界见 `docs/20-requirements/18-batch-state-layers.md`：

- **配置组定义** `BatchConfigItem`（`batch.json`）——可见范围、初始顺序、默认勾选、
  调度单元、生命周期 wf 及其参数。只在「批量配置」窗口里改。
- **运行草稿** `BatchRunDraft`（`interface.json` 的 `ui_state.batch`）——本次要跑哪些、
  按什么顺序、跑几轮、有没有人看守。主页面改它，**永不反写定义层**。
- **执行快照** `BatchRunSpec`（内存，见 `ui/batch/batch_runner.py`）——点开始那一刻
  把上面两层合成一份冻结数据，调度器只认它。

草稿按配置组的**稳定 ID** 隔离：用名称做 key 的话，重命名要搬迁状态，删掉再建
同名还会继承上一个组的草稿。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


def _unique(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


@dataclass
class BatchSelection:
    """一类候选在本次运行中的显示顺序与勾选集合。

    ``order`` 是**全部候选**的本次顺序（含未勾选的），``checked`` 是其中要执行的。
    两者必须分开：合在一起的话，取消勾选就会丢掉它的位置，重新勾上只能落到末尾。
    """

    order: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def reconcile(self, candidates: list[str], defaults: list[str]) -> None:
        """按定义层的候选与默认勾选协调本次草稿。

        规则（定义变化不该清掉用户刚调好的本次顺序）：

        1. 已不在候选里的条目丢弃——配置组里删掉了，或该属性值不存在了；
        2. 仍在候选里的条目保留草稿中的勾选与顺序；
        3. 新出现的候选按定义层顺序追加，勾选状态取定义层的默认值。

        空草稿走同一条路径：所有候选按定义顺序进来，按默认值勾选——于是
        「配置组第一次使用」和「恢复默认」不需要第二套代码。
        """
        known = set(candidates)
        default_set = set(defaults)
        order = [value for value in _unique(self.order) if value in known]
        checked = {value for value in self.checked if value in known}
        seen = set(order)
        for value in candidates:
            if value in seen:
                continue
            order.append(value)
            seen.add(value)
            if value in default_set:
                checked.add(value)
        self.order = order
        self.checked = [value for value in order if value in checked]

    @classmethod
    def from_defaults(
        cls, candidates: list[str], defaults: list[str],
    ) -> "BatchSelection":
        """按定义层重建：顺序回到初始顺序，勾选回到默认勾选。"""
        default_set = set(defaults)
        return cls(
            order=list(candidates),
            checked=[value for value in candidates if value in default_set],
        )

    def execution_order(self) -> list[str]:
        """本次实际执行顺序：按 order 过滤出勾选项。"""
        checked = set(self.checked)
        return [value for value in self.order if value in checked]

    def to_dict(self) -> dict:
        return {"order": list(self.order), "checked": list(self.checked)}

    @staticmethod
    def from_dict(data: object) -> "BatchSelection":
        source = data if isinstance(data, dict) else {}
        return BatchSelection(
            order=_unique(source.get("order")),
            checked=_unique(source.get("checked")),
        )


@dataclass
class BatchRunDraft:
    """一个配置组的本次执行草稿。

    ``units`` 按属性键分别保存：调度单元在定义层可以改来改去，切回去时还能记得
    上次在那个键下勾了哪些。
    """

    tasks: BatchSelection = field(default_factory=BatchSelection)
    users: BatchSelection = field(default_factory=BatchSelection)
    units: dict[str, BatchSelection] = field(default_factory=dict)
    rounds: int = 1
    unattended: bool = False

    def entry_selection(self, unit_key: str) -> BatchSelection:
        """当前调度单元对应的条目选择（用户名或属性值）。"""
        if unit_key == "user":
            return self.users
        return self.units.setdefault(unit_key, BatchSelection())

    def reconcile(
        self,
        *,
        task_candidates: list[str],
        task_defaults: list[str],
        entry_candidates: list[str],
        entry_defaults: list[str],
        unit_key: str,
    ) -> None:
        self.tasks.reconcile(task_candidates, task_defaults)
        self.entry_selection(unit_key).reconcile(entry_candidates, entry_defaults)
        if not isinstance(self.rounds, int) or isinstance(self.rounds, bool):
            self.rounds = 1
        self.rounds = min(999, max(1, self.rounds))
        self.unattended = bool(self.unattended)

    def to_dict(self) -> dict:
        return {
            "tasks": self.tasks.to_dict(),
            "users": self.users.to_dict(),
            "units": {
                key: value.to_dict() for key, value in self.units.items()
            },
            "rounds": self.rounds,
            "unattended": self.unattended,
        }

    @staticmethod
    def from_dict(data: object) -> "BatchRunDraft":
        source = data if isinstance(data, dict) else {}
        raw_units = source.get("units")
        units = {
            key: BatchSelection.from_dict(value)
            for key, value in raw_units.items()
            if isinstance(key, str) and key
        } if isinstance(raw_units, dict) else {}
        rounds = source.get("rounds", 1)
        return BatchRunDraft(
            tasks=BatchSelection.from_dict(source.get("tasks")),
            users=BatchSelection.from_dict(source.get("users")),
            units=units,
            rounds=rounds if isinstance(rounds, int)
            and not isinstance(rounds, bool) else 1,
            unattended=bool(source.get("unattended", False)),
        )


# ─── interface.json 的 ui_state.batch ─────────────────────────
#
# 放在 ui_state 下而不是新开顶层节点：它就是页面状态，和窗口尺寸、日常页选中项
# 同一性质。batch 内部还有活动组和多份草稿，写入必须在 SessionStore 锁内修改
# 最新节点；先 get 再整体 update 会让两个实例用各自的旧快照互相覆盖。

_UI_STATE_KEY = "ui_state"
_BATCH_SECTION = "batch"


def _batch_section() -> dict:
    from .config.interface import get_interface_store

    node = get_interface_store().get_node(_UI_STATE_KEY, {}) or {}
    section = node.get(_BATCH_SECTION) if isinstance(node, dict) else None
    return section if isinstance(section, dict) else {}


def _mutate_batch_section(mutator: Callable[[dict], None]) -> None:
    from .config.interface import get_interface_store

    def _merge(old: object) -> dict:
        state = dict(old) if isinstance(old, dict) else {}
        current = state.get(_BATCH_SECTION)
        section = dict(current) if isinstance(current, dict) else {}
        mutator(section)
        state[_BATCH_SECTION] = section
        return state

    get_interface_store().mutate_node(_UI_STATE_KEY, _merge)


def active_group_id() -> str:
    """主页面当前选中的配置组 ID。属于页面状态，不进 batch.json。"""
    value = _batch_section().get("active_group_id")
    return value if isinstance(value, str) else ""


def set_active_group_id(group_id: str) -> None:
    def _set(section: dict) -> None:
        section["active_group_id"] = str(group_id or "")

    _mutate_batch_section(_set)


def load_draft(group_id: str) -> BatchRunDraft:
    if not group_id:
        return BatchRunDraft()
    drafts = _batch_section().get("drafts")
    raw = drafts.get(group_id) if isinstance(drafts, dict) else None
    return BatchRunDraft.from_dict(raw)


def save_draft(group_id: str, draft: BatchRunDraft) -> None:
    if not group_id:
        return

    def _save(section: dict) -> None:
        drafts = section.get("drafts")
        drafts = dict(drafts) if isinstance(drafts, dict) else {}
        drafts[group_id] = draft.to_dict()
        section["drafts"] = drafts

    _mutate_batch_section(_save)


def forget_drafts(keep_group_ids: list[str]) -> None:
    """删除配置组后清掉它的草稿，避免 session 里堆积孤儿数据。"""
    keep = set(keep_group_ids)

    def _forget(section: dict) -> None:
        drafts = section.get("drafts")
        if not isinstance(drafts, dict):
            return
        section["drafts"] = {
            key: value for key, value in drafts.items() if key in keep
        }

    _mutate_batch_section(_forget)
