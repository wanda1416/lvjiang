"""批量执行的进度状态常量与数据结构。

这些结构是「控制层与业务 wf 之间的协议」：控制层只解释标准结果
status/message/state，不理解 state 内的业务字段。放在单独模块里，是为了让
阶段执行与无人值守恢复两个 mixin 都能引用它们，而不必反过来 import 编排器。
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Callable

from ...i18n import tr
from ...workflows.errors import WorkflowAbort
from ...workflows.result_protocol import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_STOPPED,
    RESULT_SUCCESS,
)
from .config import BatchConfigItem, BatchWorkflows
from .run import BatchRunDraft

# 进度状态常量
ST_PENDING = tr("待执行")
ST_RUNNING = tr("运行中")
ST_SUCCESS = tr("成功")
ST_FAILED = tr("失败")
ST_SKIPPED = tr("跳过")
ST_STOPPED = tr("已停止")
ST_CHANNEL_FAULT = tr("通道中断")
ST_NOT_RUN = tr("未执行")
_RESULT_STATUSES = {
    RESULT_SUCCESS, RESULT_SKIPPED, RESULT_FAILED, RESULT_STOPPED,
}


class BatchEvent:
    """宿主提供回调；调度器不依赖 GUI 线程或框架。"""

    def __init__(self):
        self._callbacks: list[Callable[..., object]] = []

    def connect(self, callback: Callable[..., object]) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in self._callbacks:
            callback(*args)


@dataclass
class BatchScript:
    """批量执行中的脚本描述

    不含任何参数值；parameters 只是脚本元数据中的参数定义。
    """
    id: str
    name: str
    wf_file: str = ""       # DSL 工作流文件（相对 workflows/ 或绝对路径）
    class_name: str = ""    # Python 类实现（与 wf_file 二选一）
    scope: str = "daily"    # 任务定义性质；daily / dedicated 都进入历史
    parameters: list[dict] | None = None  # 仅参数定义，不携带任何参数值
    env: list[str] | None = None  # 脚本级可运行环境，启动前二次校验
    batch_check: str = ""  # 批量执行前调用的 wf 内部子过程；空表示直接执行


@dataclass(frozen=True)
class PlannedTask:
    """批量启动时冻结的一个“用户 × 脚本”执行项。"""
    run_idx: int
    username: str
    script: BatchScript
    params: dict
    parameter_source: str


@dataclass(frozen=True)
class BatchRunSpec:
    """点下「开始」那一刻冻结的一次批量计划。

    配置组定义 + 运行草稿 → 这份快照。调度器只认它：运行期不再读 `batch.json`、
    不再读 session、也不再看主页面控件。所以启动之后去改配置组、改用户资料或
    在主页面重新勾选，都不会影响正在跑的这一批。

    `unattended` 存的是**有效值**：草稿勾了无人值守、且配置组确实配了异常恢复
    wf，才会是真。这个与运算只在构造快照时做一次，调度器不必再判断前提。

    `allow_portrait` 同样是有效值：草稿勾了允许竖屏、且配置组配了异常恢复 wf，
    才允许安卓批量在竖屏画面下启动；启动后先走一次异常恢复回到登录主页。

    生命周期参数也来自运行草稿。真实生效的键仍由 wf 文件头实时声明的参数集合
    决定，草稿里换工作流后残留的同名旧值不会生效。

    `excluded` 来自「用户声明跳过」：用户资料里的长期声明在启动那一刻被收集成
    这份快照（见 :func:`core.batch.exclusions.collect_exclusions`），此后运行期
    不再读用户资料。它是执行层的冻结值为准——启动后去改用户资料不会改变本批。
    键是用户名（用户模式下就是条目，属性单元模式下是成员），值按 task id 排序
    成元组，保证 ``asdict()`` 之后仍然可稳定序列化。
    """

    group_id: str = ""
    name: str = ""
    #: 本次执行顺序：用户模式是用户名，属性单元模式是属性值
    entries: tuple[str, ...] = ()
    #: 属性单元模式下参与分组的全部可见用户；用户模式与 entries 相同
    candidate_usernames: tuple[str, ...] = ()
    scripts: tuple[BatchScript, ...] = ()
    execution_unit_key: str = "user"
    rounds: int = 1
    workflows: BatchWorkflows = field(default_factory=BatchWorkflows)
    workflow_params: dict[str, dict] = field(default_factory=dict)
    skip_lifecycle_for_single_item: bool = True
    unattended: bool = False
    allow_portrait: bool = False
    #: 用户声明跳过的「用户 × 任务」：{用户名: 排好序的 task id 元组}。
    #: 只影响执行：任务列照旧生成，进度表照旧有这一行，状态记「跳过」。
    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @staticmethod
    def build(
        item: BatchConfigItem,
        draft: BatchRunDraft,
        *,
        entries: list[str],
        scripts: list[BatchScript],
        candidate_usernames: list[str] | None = None,
        excluded: Mapping[str, Iterable[str]] | None = None,
    ) -> "BatchRunSpec":
        return BatchRunSpec(
            group_id=item.id,
            name=item.name,
            entries=tuple(entries),
            candidate_usernames=tuple(
                entries if candidate_usernames is None else candidate_usernames),
            scripts=tuple(copy.deepcopy(scripts)),
            execution_unit_key=item.execution_unit_key,
            rounds=draft.rounds,
            workflows=copy.deepcopy(item.workflows),
            workflow_params=copy.deepcopy(draft.workflow_params),
            skip_lifecycle_for_single_item=item.skip_lifecycle_for_single_item,
            # 无人值守必须配套恢复 wf，否则改道弹窗只会让整批在错误页面上连环失败
            unattended=bool(
                draft.unattended and item.workflows.recover_unattended),
            # 允许竖屏同样离不开恢复 wf：放行后要先回到登录主页再进入游戏；
            # 也离不开条目准备——把客户端收回登录页之后只有它能再把人送进游戏，
            # 少了它整批会在登录页上逐条失败。
            allow_portrait=bool(
                draft.allow_portrait
                and item.workflows.recover_unattended
                and item.workflows.prepare_item),
            # 排序后存元组：快照会整体写进执行历史（asdict → JSON），set 既不能
            # 序列化，顺序也会随进程变化。空集合不保留键。
            excluded={
                str(user): tuple(sorted(str(task_id) for task_id in tasks if task_id))
                for user, tasks in (excluded or {}).items()
                if tasks
            },
        )


@dataclass
class BatchContext:
    """引擎创建所需的后端上下文（由 host 在启动时注入）"""
    capture: object
    ocr: object
    input_ctrl: object
    layout: object
    target_id: str = ""
    target_kind: str = ""
    target_label: str = ""
    task_run_id: str = ""
    full_task_capture: bool = False
    input_kind: str = ""
    layout_name: str = ""
    run_env: str = ""
    input_sim: object | None = None
    delay_params: dict | None = None
    android_apps: dict | None = None
    android_device: object | None = None
    window_left: int = 0
    window_top: int = 0
    pause_event: object = None  # threading.Event | None
    history_source: str = "batch"
    ui_callback: Callable[..., object] | None = None
    # 条目准备重启客户端后，宿主据此把定位状态跟到新窗口。
    window_rebind_hook: Callable[[dict], None] | None = None
    # 用户锁冲突时，宿主据此说明是哪个目标的哪个运行实例占着这个用户。
    # 用户锁本身是进程级的，不知道目标，所以必须由持有 RunManager 的宿主回答。
    user_conflict_describer: Callable[[str], str] | None = None


@dataclass(frozen=True)
class BatchStageResult:
    """生命周期 wf 的通用返回结果。state 内容对控制层完全透明。"""
    status: str = RESULT_SUCCESS
    message: str = ""
    state: dict | None = None
    username: str = ""
    #: wf 是否执行过顶层 return。跑到末尾一次 return 都没走时为 False——
    #: 对「只做事不返回」的阶段无所谓，但属性单元的条目准备必须回传 username，
    #: 这时要能把「没返回」和「返回了但内容不对」分开报。
    returned: bool = True
    #: 实际加载的 wf 路径。config/local 会顶替 system，
    #: 报错时带上它，本地覆盖这类问题一眼可见，不用去翻编辑器。
    source: str = ""


@dataclass(frozen=True)
class BatchCheckResult:
    """业务 wf 的批量可执行性判定结果。"""
    status: str = RESULT_SUCCESS
    message: str = ""


class UnattendedInterrupt(WorkflowAbort):
    """无人值守模式下工作流通过 pause 声明异常阻断。

    pause 经由 ``engine._ui_callback`` 走到宿主，所以
    无人值守只需要在这一处改道，不必去改 wf 里几十个调用点。抛出后当前任务
    按异常记失败、不重试，调度器随即调用配置的恢复 wf 把游戏收回公共初始页，
    再继续下一个任务。
    """
