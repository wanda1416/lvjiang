"""方案（Plan）—— 机器级的「图库 + 环境 + 布局 + 连接模式」组合

解决的问题：图库、环境、布局三者必须配套使用，代码里却互不认识，切一次
目标要点三下；而连接模式（窗口 / ADB）既不持久化也无人校验，于是「连了
ADB 却还用着端游那套组合」时，坐标原点按窗口算，点击整体偏移且不报错。

方案把这四者绑成一个具名整体，并声明自己支持哪些连接模式。

方案存两处，由 ``distributed`` 标志决定去向：

- ``distributed=False``（默认）→ session.json 顶层 ``plans`` 节点，属于
  这台机器自己的配置；
- ``distributed=True`` → app.yaml 顶层 ``plans`` 键，随安装包分发。开发者
  据此挑选要预置的方案，而不必把机器上所有方案都塞进发行配置。

``distributed`` 不落盘：它就是「这条方案存在哪」的同义词，从加载来源推导
出来，不会与实际存储位置脱节。同一条方案在两处只能存在一份——保存时按标
志分流，写入一边即从另一边移除。

app.yaml 里的方案对普通用户只读（改动 system 层是开发者的职责，见
:func:`ConfigResolver.is_dev_mode`）；当前选中项一律记在 ``actives.plan``。
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
#: app.yaml 里存放分发方案的顶层键，与 session.json 的节点同名。
_APP_PLANS_KEY = "plans"

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
    #: 是否随安装包分发（存 app.yaml 而非 session.json）。不序列化：
    #: 由加载来源推导，见模块 docstring。
    distributed: bool = False

    @classmethod
    def create(cls, name: str, *, space: str = "", env: str = "",
               layout: str = "", modes: Sequence[str] | None = None,
               distributed: bool = False) -> Plan:
        return cls(
            id=uuid4().hex,
            name=name,
            space=space,
            env=env,
            layout=layout,
            modes=_clean_modes(modes),
            distributed=distributed,
        )

    @classmethod
    def from_dict(cls, data: object, *, distributed: bool = False) -> Plan | None:
        """脏数据返回 None（调用方跳过该条），不抛异常。

        ``distributed`` 由调用方按加载来源指定，不从 data 里读。
        """
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
            distributed=distributed,
        )

    def to_dict(self) -> dict:
        """落盘形态。刻意不含 ``distributed``——存在哪就是哪。"""
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


def _load_distributed_raw() -> list:
    """app.yaml 顶层 plans 的原始列表；缺失或类型不对时视为空。"""
    from .resolver import load_app_config

    raw = load_app_config().get(_APP_PLANS_KEY)
    return raw if isinstance(raw, list) else []


def _save_distributed_raw(items: list) -> None:
    """整体替换 app.yaml 的 plans 键。

    内容没变就不写：普通用户不会改动分发方案，但每次保存设置都会走到这里，
    真写下去会在 local 层留下一份毫无意义的 app.yaml 覆盖。
    """
    from .resolver import load_app_config, save_app_config_node

    if _load_distributed_raw() == items:
        return
    if not items and _APP_PLANS_KEY not in load_app_config():
        return
    save_app_config_node(_APP_PLANS_KEY, items)


def _parse_plans(raw: object, *, distributed: bool,
                 seen: set[str]) -> list[Plan]:
    """把一层原始列表解析成方案；损坏条目跳过并记日志。"""
    if not isinstance(raw, list):
        return []
    plans: list[Plan] = []
    for item in raw:
        plan = Plan.from_dict(item, distributed=distributed)
        if plan is None:
            logger.warning(f"跳过损坏的方案配置: {item!r}")
            continue
        if plan.id in seen:
            logger.warning(f"跳过重复 id 的方案: {plan.id}")
            continue
        seen.add(plan.id)
        plans.append(plan)
    return plans


def load_plans() -> list[Plan]:
    """读取全部方案：先 app.yaml 的分发方案，再 session.json 的本机方案。

    分发方案排在前面，让预置方案在列表里稳定居首；id 跨两层去重，重复只留
    先出现的那条（即分发方案胜出——同 id 的本机副本是历史迁移残留）。
    """
    seen: set[str] = set()
    return (
        _parse_plans(_load_distributed_raw(), distributed=True, seen=seen)
        + _parse_plans(get_session_store().get_node(_PLANS_NODE),
                       distributed=False, seen=seen)
    )


def save_plans(plans: Sequence[Plan]) -> None:
    """整体替换方案列表，按 ``distributed`` 分流到两个存储。

    两边都整体重写，所以一条方案改了标志就会自动从原来那边消失——不需要
    额外的迁移步骤，也不会两处各留一份。
    """
    get_session_store().set_node(
        _PLANS_NODE,
        [plan.to_dict() for plan in plans if not plan.distributed])
    _save_distributed_raw(
        [plan.to_dict() for plan in plans if plan.distributed])


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
