"""方案（Plan）—— 机器级的「图库 + 环境 + 布局 + 连接模式」组合

解决的问题：图库、环境、布局三者必须配套使用，代码里却互不认识，切一次
目标要点三下；而连接模式（窗口 / ADB）既不持久化也无人校验，于是「连了
ADB 却还用着端游那套组合」时，坐标原点按窗口算，点击整体偏移且不报错。

方案把这四者绑成一个具名整体，并声明自己支持哪些连接模式。

存储在 session.json 顶层 ``plans`` 节点，当前选中项在 ``actives.plan``。
方案是**机器级**的——描述「这台机器怎么连游戏」，与游戏账号无关，切换
用户不影响方案。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from loguru import logger

from .session import get_session_store

_PLANS_NODE = "plans"

# 连接模式取值刻意与主窗口的 ``self._backend`` 一致（"windows" 指 Win32
# 窗口投屏，不是操作系统），这样判定就是一次成员检查，不需要翻译层。
PLAN_MODE_WINDOW = "windows"
PLAN_MODE_ADB = "adb"
PLAN_MODES = (PLAN_MODE_WINDOW, PLAN_MODE_ADB)


@dataclass
class Plan:
    """一套具名的运行目标组合。

    id 与 name 分离：重命名不会破坏 ``actives.plan`` 的引用。
    """

    id: str
    name: str
    space: str = ""
    env: str = ""
    layout: str = ""
    # 支持的连接模式。空表示不限制——损坏的配置绝不能把用户锁在
    # 「开始执行」之外。
    modes: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, name: str, *, space: str = "", env: str = "",
               layout: str = "", modes: Sequence[str] | None = None) -> Plan:
        return cls(
            id=uuid4().hex,
            name=name,
            space=space,
            env=env,
            layout=layout,
            modes=_clean_modes(modes),
        )

    @classmethod
    def from_dict(cls, data: object) -> Plan | None:
        """脏数据返回 None（调用方跳过该条），不抛异常。"""
        if not isinstance(data, dict):
            return None
        plan_id = data.get("id")
        name = data.get("name")
        if not isinstance(plan_id, str) or not plan_id:
            return None
        if not isinstance(name, str) or not name:
            return None
        return cls(
            id=plan_id,
            name=name,
            space=_clean_str(data.get("space")),
            env=_clean_str(data.get("env")),
            layout=_clean_str(data.get("layout")),
            modes=_clean_modes(data.get("modes")),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "space": self.space,
            "env": self.env,
            "layout": self.layout,
            "modes": list(self.modes),
        }

    def allows(self, backend: str | None) -> bool:
        """该方案是否支持当前连接模式。

        modes 为空（未勾选或配置损坏）时不限制；backend 未知时同样放行，
        真正的「没连上」由 ``_backend_ready()`` 负责，不归方案管。
        """
        if not self.modes or not backend:
            return True
        return backend in self.modes


def _clean_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _clean_modes(value: object) -> list[str]:
    """只保留已知模式并去重，顺序按 PLAN_MODES 固定。"""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    picked = {item for item in value if item in PLAN_MODES}
    return [mode for mode in PLAN_MODES if mode in picked]


def load_plans() -> list[Plan]:
    """读取全部方案；损坏条目跳过并记日志，重复 id 只留第一条。"""
    raw = get_session_store().get_node(_PLANS_NODE)
    if not isinstance(raw, list):
        return []
    plans: list[Plan] = []
    seen: set[str] = set()
    for item in raw:
        plan = Plan.from_dict(item)
        if plan is None:
            logger.warning(f"跳过损坏的方案配置: {item!r}")
            continue
        if plan.id in seen:
            logger.warning(f"跳过重复 id 的方案: {plan.id}")
            continue
        seen.add(plan.id)
        plans.append(plan)
    return plans


def save_plans(plans: Sequence[Plan]) -> None:
    """整体替换方案列表。"""
    get_session_store().set_node(
        _PLANS_NODE, [plan.to_dict() for plan in plans])


def get_active_plan_id() -> str:
    """当前选中的方案 id；空串表示「自定义」。"""
    value = get_session_store().get_active("plan", "")
    return value if isinstance(value, str) else ""


def set_active_plan_id(plan_id: str) -> None:
    get_session_store().set_active("plan", plan_id or "")


def get_active_plan() -> Plan | None:
    """当前选中的方案；未选或 id 已失效时返回 None（即「自定义」）。"""
    plan_id = get_active_plan_id()
    if not plan_id:
        return None
    for plan in load_plans():
        if plan.id == plan_id:
            return plan
    return None
