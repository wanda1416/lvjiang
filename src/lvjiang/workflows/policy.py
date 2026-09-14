"""脚本发现与暴露策略

三层职责划分：

1. **脚本全集** —— 由作者在 ``.wf`` 的 ``#%`` front-matter 里声明
   ``runnable`` / ``batchable`` 决定。**未声明即不可注册、不可批量。**
2. **是否默认展示** —— 由作者声明：``hidden`` 的脚本和 ``dedicated``
   专用脚本默认不展示；用户仍可在脚本配置中显式打开。
3. **顺序、启停、显示名** —— 属于用户偏好，存 session 的 ``daily`` 节点，
   不写回系统配置。

本模块**不含任何目录约定**。发现层递归扫描 ``workflows/`` 全树，目录只用
于组织文件，不表达语义——所以脚本可以按业务自由分目录，把「哪些
能独立跑、哪些能排进批量」这类性质写在文件自己的元数据里。

唯一的例外是 ``_`` 前缀：那是编辑器临时运行与录制产物，属于「磁盘上的临时
品」而不是「作者意图」，跟它放在哪个目录无关。
"""

from __future__ import annotations

from pathlib import PurePosixPath


class WorkflowDiscoveryPolicy:
    """脚本发现策略（全部为类属性/类方法，无需实例化）"""

    #: 内部文件名 / 目录名前缀（编辑器临时运行、录制产物）
    INTERNAL_PREFIXES: tuple[str, ...] = ("_",)

    #: front-matter 里表示「默认不展示」的键 / 类实现对应的类属性名
    HIDDEN_META_KEY = "hidden"
    HIDDEN_CLASS_ATTR = "HIDDEN"

    #: 同 id 时的来源优先级，下标越小越优先。
    #:
    #: - ``local`` 最高：用户自己的东西，任何在线下发都不该盖掉。
    #: - ``class`` 次之：内置实现，代码即事实。
    #: - ``remote`` 最低：在线下发只新增，永不抢占随包或用户脚本。
    SOURCE_PRIORITY: tuple[str, ...] = ("local", "class", "system", "remote")

    @classmethod
    def is_internal(cls, rel_path: str) -> bool:
        """是否为内部文件（不进入脚本全集）

        路径**任一段**以 ``_`` 开头即算内部：``_editor_run.wf`` 与
        ``subcall/_draft/x.wf`` 都不该被注册成脚本。
        """
        return any(part.startswith(cls.INTERNAL_PREFIXES)
                   for part in rel_path.split("/"))

    @classmethod
    def rank(cls, source_layer: str) -> int:
        """来源层优先级：数字越小越优先；未登记的来源排在最后。"""
        try:
            return cls.SOURCE_PRIORITY.index(source_layer)
        except ValueError:
            return len(cls.SOURCE_PRIORITY)

    @classmethod
    def hidden_by_default(cls, meta: dict) -> bool:
        """作者是否声明了默认不展示（用户仍可在脚本配置里手动打开）"""
        return bool(meta.get(cls.HIDDEN_META_KEY, False))

    @classmethod
    def visible_by_default(cls, *, hidden: bool, scope: str) -> bool:
        """脚本未隐藏且属于日常范围时，才默认暴露在通用入口。"""
        return not hidden and scope != "dedicated"

    @staticmethod
    def default_id_for(rel_path: str) -> str:
        """``standalone/foo.wf`` 和 ``foo.wf`` 均取普通 id ``foo``。

        目录只组织文件，不进入逻辑 id。同名脚本需要在 front-matter
        显式声明不同的标准 id。
        """
        return PurePosixPath(rel_path).stem
