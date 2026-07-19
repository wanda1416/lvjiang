"""材料数据库 - 基于 materials.yaml 的材料元数据管理

materials.yaml 是材料参考库的唯一数据源，存放于 config/system/ 目录下。
图片文件按分组存放于 data/materials/{group}/ 子目录中。
每个条目记录相对文件路径、材料类型、等级等元数据，与图片文件一一对应。
"""

import re
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml
from loguru import logger


# 默认路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_YAML_PATH = _PROJECT_ROOT / "config" / "system" / "materials.yaml"
_DEFAULT_MATERIALS_DIR = _PROJECT_ROOT / "data" / "materials"


@dataclass
class MaterialEntry:
    """单条材料记录"""
    file: str          # 图片相对路径（如 "调律材料/001.png"）
    type: str = ""     # 材料类型（如 "定音石"）
    level: int | None = None  # 等级（如 100），无等级为 None
    group: str = ""    # 分组（如 "调律材料"、"狗粮"）
    source: str = ""   # 来源截图名（可选）
    notes: str = ""    # 备注


class MaterialDatabase:
    """材料数据库 - 管理 materials.yaml 的读写与条目操作

    用法：
        db = MaterialDatabase()
        db.load()
        db.add_entry(type="定音石", level=100, group="调律材料", image_data=bgr_array)
        db.save()
    """

    def __init__(
        self,
        materials_dir: Path | str | None = None,
        yaml_path: Path | str | None = None,
    ):
        self._dir = Path(materials_dir) if materials_dir else _DEFAULT_MATERIALS_DIR
        self._yaml_path = Path(yaml_path) if yaml_path else _DEFAULT_YAML_PATH
        self._entries: list[MaterialEntry] = []
        self._loaded = False

    # ─── 属性 ────────────────────────────────────────────

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def yaml_path(self) -> Path:
        return self._yaml_path

    @property
    def entries(self) -> list[MaterialEntry]:
        self._ensure_loaded()
        return list(self._entries)

    # ─── 加载 / 保存 ─────────────────────────────────────

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def load(self):
        """从 materials.yaml 加载条目列表"""
        if not self._yaml_path.exists():
            logger.info(f"materials.yaml 不存在，使用空数据库: {self._yaml_path}")
            self._entries = []
            self._loaded = True
            return

        with open(self._yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "materials" not in data:
            self._entries = []
            self._loaded = True
            return

        self._entries = []
        for item in data["materials"]:
            entry = MaterialEntry(
                file=item.get("file", ""),
                type=item.get("type", ""),
                level=item.get("level"),
                group=item.get("group", ""),
                source=item.get("source", ""),
                notes=item.get("notes", ""),
            )
            self._entries.append(entry)

        self._loaded = True
        logger.info(f"材料数据库加载完成: {len(self._entries)} 条记录 <- {self._yaml_path}")

    def save(self):
        """保存条目列表到 materials.yaml"""
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "materials": [asdict(e) for e in self._entries],
        }

        with open(self._yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"材料数据库保存完成: {len(self._entries)} 条记录 -> {self._yaml_path}")

    # ─── 条目操作 ─────────────────────────────────────────

    def add_entry(
        self,
        type: str,
        level: int | None = None,
        group: str = "",
        source: str = "",
        notes: str = "",
        image_data: "np.ndarray | None" = None,
    ) -> MaterialEntry:
        """新增材料条目

        Args:
            type: 材料类型
            level: 等级（可选）
            group: 分组（图片按此字段存入子目录）
            source: 来源截图名
            notes: 备注
            image_data: 如果提供，自动保存为 PNG 文件

        Returns:
            新建的 MaterialEntry
        """
        filename = self.next_filename(group)
        entry = MaterialEntry(
            file=filename,
            type=type,
            level=level,
            group=group,
            source=source,
            notes=notes,
        )

        # 保存图片文件
        if image_data is not None:
            self._save_image(filename, image_data)

        self._entries.append(entry)
        self.save()
        logger.debug(f"新增材料: {filename} type={type} level={level} group={group}")
        return entry

    def update_entry(self, filename: str, **kwargs) -> bool:
        """修改条目元数据

        如果 group 变更，会自动移动图片文件到新分组目录。

        Args:
            filename: 目标文件路径（相对路径）
            **kwargs: 要修改的字段（type, level, group, source, notes）

        Returns:
            是否找到并修改
        """
        for entry in self._entries:
            if entry.file == filename:
                old_group = entry.group
                for key, value in kwargs.items():
                    if hasattr(entry, key):
                        setattr(entry, key, value)

                # 如果 group 变更，移动图片文件
                new_group = entry.group
                if new_group != old_group:
                    self._move_image(filename, old_group, new_group)
                    # 更新 file 路径
                    old_name = Path(filename).name
                    entry.file = f"{new_group}/{old_name}" if new_group else old_name

                self.save()
                logger.debug(f"更新材料: {filename} {kwargs}")
                return True
        logger.warning(f"未找到材料文件: {filename}")
        return False

    def remove_entry(self, filename: str, delete_file: bool = True) -> bool:
        """删除条目

        Args:
            filename: 目标文件路径（相对路径）
            delete_file: 是否同时删除图片文件

        Returns:
            是否找到并删除
        """
        for i, entry in enumerate(self._entries):
            if entry.file == filename:
                self._entries.pop(i)
                if delete_file:
                    file_path = self._dir / filename
                    if file_path.exists():
                        file_path.unlink()
                        logger.debug(f"删除材料文件: {file_path}")
                self.save()
                logger.debug(f"删除材料条目: {filename}")
                return True
        return False

    # ─── 查询 ─────────────────────────────────────────────

    def list_materials(
        self,
        type_filter: str | None = None,
        group_filter: str | None = None,
        level_filter: int | None = None,
    ) -> list[MaterialEntry]:
        """返回材料条目列表

        Args:
            type_filter: 按类型过滤，None 返回全部
            group_filter: 按分组过滤，None 返回全部
            level_filter: 按等级过滤，None 返回全部
        """
        self._ensure_loaded()
        result = list(self._entries)
        if type_filter:
            result = [e for e in result if e.type == type_filter]
        if group_filter:
            result = [e for e in result if e.group == group_filter]
        if level_filter is not None:
            result = [e for e in result if e.level == level_filter]
        return result

    def get_types(self) -> list[str]:
        """返回所有去重类型列表（排序）"""
        self._ensure_loaded()
        types = sorted({e.type for e in self._entries if e.type})
        return types

    def get_types_by_group(self, group: str) -> list[str]:
        """返回指定分组下的去重类型列表（排序）"""
        self._ensure_loaded()
        types = sorted({e.type for e in self._entries if e.type and e.group == group})
        return types

    def get_all_types_by_group(self) -> dict[str, list[str]]:
        """返回所有分组 -> 类型列表的映射"""
        self._ensure_loaded()
        result: dict[str, set[str]] = {}
        for e in self._entries:
            if e.group and e.type:
                if e.group not in result:
                    result[e.group] = set()
                result[e.group].add(e.type)
        return {g: sorted(types) for g, types in result.items()}

    def get_groups(self) -> list[str]:
        """返回所有去重分组列表（排序）"""
        self._ensure_loaded()
        groups = sorted({e.group for e in self._entries if e.group})
        return groups

    def get_levels(self) -> list[int]:
        """返回所有去重等级列表（升序）"""
        self._ensure_loaded()
        levels = sorted({e.level for e in self._entries if e.level is not None})
        return levels

    def get_references(self, mat_type: str) -> list[MaterialEntry]:
        """返回某类型下所有条目（含 file、level）"""
        self._ensure_loaded()
        return [e for e in self._entries if e.type == mat_type]

    def get_entry(self, filename: str) -> MaterialEntry | None:
        """按文件路径查找条目"""
        self._ensure_loaded()
        for e in self._entries:
            if e.file == filename:
                return e
        return None

    def next_filename(self, group: str = "") -> str:
        """返回新的文件路径（如 '调律材料/A1B2C3D4.png'）

        使用 UUID 前 8 位（大写字母）作为文件名，防止重复。
        """
        name = f"{uuid.uuid4().hex[:8].upper()}.png"
        return f"{group}/{name}" if group else name

    # ─── 内部方法 ─────────────────────────────────────────

    def _save_image(self, rel_path: str, image_data: "np.ndarray"):
        """保存 numpy 图像到文件（按相对路径，支持中文路径）"""
        import cv2
        file_path = self._dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite 不支持中文路径，用 imencode + 文件写入
        success, buf = cv2.imencode('.png', image_data)
        if success:
            file_path.write_bytes(buf.tobytes())
            logger.debug(f"保存材料图片: {file_path} ({image_data.shape[1]}x{image_data.shape[0]})")
        else:
            logger.error(f"图片编码失败: {file_path}")

    def _move_image(self, rel_path: str, old_group: str, new_group: str):
        """移动图片文件到新分组目录"""
        old_path = self._dir / rel_path
        if not old_path.exists():
            return

        name = old_path.name
        new_dir = self._dir / new_group if new_group else self._dir
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / name

        old_path.rename(new_path)
        logger.debug(f"移动材料文件: {old_path} -> {new_path}")
