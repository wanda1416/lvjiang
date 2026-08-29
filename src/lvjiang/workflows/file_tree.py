"""工作流文件树 —— 屏蔽配置分层的合并视图

编辑器要展示的是「有哪些脚本」，而不是「local 里有哪些、system 里有哪些」。
所以这里把两层合并成一棵虚拟目录树，每个文件带上它实际来自哪一层、能不能
编辑、是否覆盖了出厂版本。

三条语义直接来自配置层，不在这里另立一套：

- **local 影子优先**：同名文件 local 完全顶掉 system（整文件替换，不做
  内容合并）。所以树上一个路径只对应一个节点，显示实际生效的那一份。
- **出厂只读**：用户模式下 system 文件不可改名/删除（``SystemContentProtected``）。
  想改必须先复制到 local——复制之后该文件就脱离出厂更新了，这个代价要让
  用户看得见，所以 :class:`WorkflowFile` 显式区分 ``overrides_system``。
- **不做墓碑**：用户不能删出厂脚本。要让它不出现，用的是「展示勾选」那套
  暴露机制，不是从磁盘上抹掉。

树**不做任何过滤**：磁盘上有什么就显示什么，``_`` 前缀的编辑器临时文件与
录制产物也在内——它们同样是用户可能要打开的文件，藏起来只会让人找不到自己
刚录的东西。「哪些脚本能独立启动」「显示名叫什么」属于发现层与元数据，是
另一个层面的事，不在这棵树里体现。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config.resolver import (
    LAYER_LOCAL,
    LAYER_SYSTEM,
    get_resolver,
)

#: 工作流根（相对配置层根）
WORKFLOWS_DIR = "workflows"
_PATTERN = "*.wf"


@dataclass(frozen=True)
class WorkflowFile:
    """合并视图里的一个 ``.wf``。"""

    rel_path: str          # 相对 workflows 根的 posix 路径
    layer: str             # 实际生效的层：local / system
    overrides_system: bool  # local 覆盖了同名出厂文件

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def parent(self) -> str:
        """所在目录（相对 workflows 根），顶层为空串。"""
        return self.rel_path.rsplit("/", 1)[0] if "/" in self.rel_path else ""

    @property
    def is_system(self) -> bool:
        return self.layer == LAYER_SYSTEM

    @property
    def editable(self) -> bool:
        """能否直接编辑。

        出厂文件只读——用户要改得先「复制到本地」。开发模式下 system 本就
        可写，由调用方按 ``resolver.is_dev_mode()`` 决定是否放开。
        """
        return self.layer == LAYER_LOCAL


def list_workflow_files() -> list[WorkflowFile]:
    """列出合并视图中的全部 ``.wf``，按路径排序。

    不过滤：``_`` 前缀、``archived/`` 等一律照实展示。
    """
    resolver = get_resolver()
    files: list[WorkflowFile] = []
    for rel in resolver.enumerate_entity_tree(
            WORKFLOWS_DIR, _PATTERN, include_internal=True):
        origin = resolver.describe_entity(f"{WORKFLOWS_DIR}/{rel}")
        if not origin.layer:
            continue          # 被墓碑遮住或读不到，不进树
        files.append(WorkflowFile(
            rel_path=rel,
            layer=origin.layer,
            overrides_system=(
                origin.layer == LAYER_LOCAL
                and (resolver.system_dir / WORKFLOWS_DIR / rel).is_file()
            ),
        ))
    return files


def list_directories(files: list[WorkflowFile] | None = None) -> list[str]:
    """树中出现过的目录（相对 workflows 根，不含顶层空串），按路径排序。"""
    files = list_workflow_files() if files is None else files
    dirs: set[str] = set()
    for f in files:
        parts = f.rel_path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)
