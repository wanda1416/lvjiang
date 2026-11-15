"""参考图库的图库空间管理：目录扫描、激活空间、增删与命名规则。

图库空间是图库内部完全独立的配置集，各自拥有 meta_schema、引用条目与参考图。
空间列表不维护名册文件——扫描 system 与 local 两层 ``references/*.yaml``，
文件名即空间名；system 层出现过的名字就是「系统空间」，用户层只能覆盖内容，
不能删除该空间。

本模块只负责「有哪些空间、当前激活哪个、怎么增删」，条目的加载、合并与写入
在 :mod:`.database` 里；需要重载时通过 ``on_reload`` 回调交回给调用方。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from lvjiang.core.config import load_yaml, save_yaml

from ...i18n import tr
from .models import DEFAULT_SPACE


class ReferenceSpaces:
    """空间列表与激活空间，以及空间层的 yaml / 图片目录增删。"""

    def __init__(
        self,
        *,
        system_root: Callable[[], Path],
        local_root: Callable[[], Path],
        is_dev: Callable[[], bool],
        interface_store: Callable[[], object],
        default_space: str = DEFAULT_SPACE,
    ):
        self._system_root = system_root
        self._local_root = local_root
        self._is_dev = is_dev
        self._interface_store = interface_store
        self._default = default_space
        self.names: list[str] = []
        self.system_names: set[str] = set()
        self.active: str = default_space

    # ─── 目录与持久化 ───

    @staticmethod
    def scan(spaces_dir: Path) -> list[str]:
        """扫描空间根目录下的 *.yaml，文件名即空间名（按名排序）。

        目录即事实：不再维护独立名册，作者新增/用户新建都只落一个空间 yaml。
        """
        if not spaces_dir.is_dir():
            return []
        names = {
            f.stem.strip() for f in spaces_dir.glob("*.yaml")
            if f.is_file() and f.stem.strip()
        }
        return sorted(names)

    @staticmethod
    def warn_legacy_roster(layer_dir: Path, default_space: str = DEFAULT_SPACE) -> None:
        """旧版 references.yaml 已作废：存在即提示，内容一律忽略。"""
        legacy = layer_dir / "references.yaml"
        if not legacy.exists():
            return
        try:
            data = load_yaml(legacy)
        except Exception:
            data = {}
        if data.get("references") is not None or data.get("meta_schema") is not None:
            logger.error(
                f"{legacy} 是更早的单空间覆盖层格式且已不再读取，"
                f"请手动改名为 references/{default_space}.yaml 以保留数据"
            )
        else:
            logger.info(f"空间名册已作废（空间改为扫描目录得出），可删除: {legacy}")

    def read_active(self) -> str:
        """读取 actives.space（interface.json）。"""
        value = self._interface_store().get_active("space")
        return str(value) if value else ""

    def write_active(self, name: str) -> None:
        """写 actives.space（interface.json）。"""
        self._interface_store().set_active("space", name)

    # ─── 列表与激活 ───

    def refresh(self) -> None:
        """重扫两层空间根目录，重建空间列表与系统空间集合。"""
        system_spaces = self.scan(self._system_root())
        local_spaces = self.scan(self._local_root())
        self.system_names = set(system_spaces)
        merged = list(system_spaces)
        merged.extend(s for s in local_spaces if s not in self.system_names)
        self.names = merged or [self._default]

    def resolve_active(self) -> None:
        """解析激活空间：已记录的 → DEFAULT_SPACE → 首个 → DEFAULT_SPACE。"""
        saved = self.read_active()
        if saved and saved in self.names:
            self.active = saved
        elif self._default in self.names:
            self.active = self._default
        else:
            self.active = self.names[0] if self.names else self._default

    def is_system(self, name: str) -> bool:
        """该空间是否由 system 层定义（用户层只能覆盖不能删除）。"""
        return str(name or "").strip() in self.system_names

    def is_user_mode(self) -> bool:
        """当前是否为用户模式（写 local 层；系统内容只能覆盖不能删除）。"""
        return not self._is_dev()

    def set_active(self, name: str) -> bool:
        """切换激活空间并持久化；非法名拒绝。

        调用方负责在成功后重载条目（旧空间的内存态不能写进新空间 yaml）。
        """
        name = str(name or "").strip()
        if name not in self.names:
            logger.warning(f"图库空间不存在，无法切换: {name!r}")
            return False
        self.write_active(name)
        self.active = name
        logger.info(f"图库空间已切换: {name}")
        return True

    # ─── 增删 ───

    def create(self, name: str) -> bool:
        """新建空图库空间（空 meta_schema + 空条目），按模式落盘即可见。"""
        name = str(name or "").strip()
        if not self.valid_name(name):
            return False
        if name in self.names:
            logger.warning(f"图库空间已存在: {name}")
            return False
        base = self._system_root() if self._is_dev() else self._local_root()
        space_yaml = base / f"{name}.yaml"
        if not space_yaml.exists():
            save_yaml(space_yaml, {
                "version": 1,
                "meta_schema": [],
                "references": [],
            })
        self.refresh()
        logger.info(f"新建图库空间: {name} -> {space_yaml}")
        return True

    def can_delete(self, name: str) -> str:
        """返回该空间**不可**删除的原因；可删则返回空串。

        UI 用它决定按钮禁用与提示文案，``delete`` 用它做最终把关。
        """
        name = str(name or "").strip()
        if name not in self.names:
            return tr("空间不存在")
        if self.is_user_mode() and self.is_system(name):
            return tr("系统空间不可删除，可新建自己的空间")
        if len(self.names) <= 1:
            return tr("至少保留一个图库空间")
        return ""

    def delete(self, name: str) -> tuple[bool, bool]:
        """删除图库空间（yaml + 同名图片目录）。

        用户模式只清 local 层且拒绝系统空间；开发模式两层一起清，避免 system 层
        删掉后残留 local 覆盖层变成孤儿空间。

        返回 ``(是否删除, 激活空间是否因此改变)``；后者为真时调用方要重载条目。
        """
        name = str(name or "").strip()
        if not self.valid_name(name):
            return False, False
        reason = self.can_delete(name)
        if reason:
            logger.warning(f"拒绝删除图库空间 {name!r}: {reason}")
            return False, False
        layers = [self._local_root()]
        if self._is_dev():
            layers.append(self._system_root())
        for layer in layers:
            self._remove_files(layer, name)
        self.refresh()
        active_changed = False
        if self.active == name:      # 激活空间被删：改激活，由调用方重载
            self.resolve_active()
            self.write_active(self.active)
            active_changed = True
        logger.info(f"已删除图库空间: {name}（当前激活={self.active}）")
        return True, active_changed

    @staticmethod
    def _remove_files(spaces_dir: Path, name: str) -> None:
        """删除某一层的空间 yaml 与同名图片目录（不存在则跳过）。"""
        space_yaml = spaces_dir / f"{name}.yaml"
        if space_yaml.is_file():
            space_yaml.unlink()
            logger.info(f"删除空间 yaml: {space_yaml}")
        image_dir = spaces_dir / name
        if image_dir.is_dir():
            shutil.rmtree(image_dir)
            logger.info(f"删除空间图片目录: {image_dir}")

    @staticmethod
    def valid_name(name: str) -> bool:
        """空间名即文件名：拒空、拒路径分隔符、拒 . 开头（隐藏文件）。"""
        if not name:
            logger.warning("图库空间名不能为空")
            return False
        if name.startswith(".") or set(name) & set('/\\:*?"<>|'):
            logger.warning(f"图库空间名非法（不能含路径分隔符或以 . 开头）: {name!r}")
            return False
        return True
