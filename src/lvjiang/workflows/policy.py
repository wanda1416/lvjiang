"""脚本发现与暴露策略

把「扫哪些目录、什么名字算内部脚本、什么算默认不展示」这些约定集中在一个
类里，后续要放开新目录或加新的隐藏规则只改这里，不用翻发现层的实现。

三层职责划分：

1. **脚本全集** —— 由本策略的目录约定决定，不可配置。
2. **是否默认展示** —— 由作者声明：``hidden`` 的脚本和 ``dedicated``
   专用脚本默认不展示；用户仍可在脚本配置中显式打开。
3. **顺序、启停、显示名** —— 属于用户偏好，存 session 的 ``daily`` 节点，
   不写回系统配置。
"""

from __future__ import annotations


class WorkflowDiscoveryPolicy:
    """脚本发现策略（全部为类属性/类方法，无需实例化）"""

    #: 可直接由用户启动的脚本目录（相对 ``workflows/``，``""`` 为顶层）。
    #: 显式列举可避免把 subcall、batch 生命周期等不能独立执行的实现文件
    #: 误注册成脚本。
    SCAN_DIRS: tuple[str, ...] = ("", "standalone")

    #: 不参与批量任务的目录（脚本本身能独立跑，但不适合排进批处理）
    NON_BATCHABLE_DIRS: tuple[str, ...] = ("standalone",)

    #: 内部脚本名前缀：这些文件不进入脚本全集（编辑器临时运行、录制产物等）
    INTERNAL_PREFIXES: tuple[str, ...] = ("_",)

    #: front-matter / 类属性里表示「默认不展示」的键
    HIDDEN_META_KEY = "hidden"
    HIDDEN_CLASS_ATTR = "HIDDEN"

    @classmethod
    def is_internal(cls, script_id: str) -> bool:
        """是否为内部脚本（不进入脚本全集）"""
        return script_id.startswith(cls.INTERNAL_PREFIXES)

    @classmethod
    def is_batchable(cls, subdir: str) -> bool:
        """该目录下的脚本能否参与批量任务"""
        return subdir not in cls.NON_BATCHABLE_DIRS

    @classmethod
    def hidden_by_default(cls, meta: dict) -> bool:
        """作者是否声明了默认不展示（用户仍可在脚本配置里手动打开）"""
        return bool(meta.get(cls.HIDDEN_META_KEY, False))

    @classmethod
    def visible_by_default(cls, *, hidden: bool, scope: str) -> bool:
        """脚本未隐藏且属于日常范围时，才默认暴露在通用入口。"""
        return not hidden and scope != "dedicated"
