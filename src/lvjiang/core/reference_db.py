"""图像参考库 - 基于 references.yaml 的参考图元数据管理

references.yaml 是参考图库的唯一数据源，存放于 config/system/ 目录下。
图片文件按分组存放于 data/references/{group}/ 子目录中。
每个条目记录相对文件路径、主标识、任意元数据，与图片文件一一对应。
"""

import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml
from loguru import logger


# 默认路径
from lvjiang.constants import PROJECT_ROOT as _PROJECT_ROOT
_DEFAULT_YAML_PATH = _PROJECT_ROOT / "config" / "system" / "references.yaml"
_DEFAULT_REFERENCES_DIR = _PROJECT_ROOT / "data" / "references"


# meta_schema 缺失时的种子字段（兼容现有数据：等级作为默认可筛选 meta 字段）
_SEED_META_SCHEMA = [
    {"key": "level", "name": "等级", "filterable": True, "type": "number", "sort_by": "asc"},
]


@dataclass
class MetaFieldDef:
    """meta 字段定义

    Attributes:
        key: 内部标识（如 "level"）
        name: 显示名（如 "等级"）
        filterable: 是否在筛选栏展示
        type: 值类型，"text" 或 "number"
        sort_by: 排序方向，"asc" 或 "desc"
    """
    key: str
    name: str = ""
    filterable: bool = False
    type: str = "text"
    sort_by: str = "asc"


@dataclass
class ReferenceEntry:
    """单条参考图记录
    
    Attributes:
        file: 图片相对路径（如 "调律材料/001.png"）
        label: 主标识（如 "定音石"、"Boss图标"）
        meta: 任意元数据（如 {"level": 100, "group": "调律材料"}）
        source: 来源截图名（可选）
        notes: 备注
    """
    file: str
    label: str = ""
    meta: dict = field(default_factory=dict)
    source: str = ""
    notes: str = ""

    # ─── 便捷属性（访问 meta 中的常用字段）─────────────────
    
    @property
    def group(self) -> str:
        """元数据中的分组字段"""
        return self.meta.get("group", "")
    
    @group.setter
    def group(self, value: str):
        self.meta["group"] = value

    @property
    def level(self) -> int | None:
        """元数据中的等级字段"""
        return self.meta.get("level")
    
    @level.setter
    def level(self, value: int | None):
        if value is None:
            self.meta.pop("level", None)
        else:
            self.meta["level"] = value


class ReferenceDatabase:
    """图像参考库 - 管理 references.yaml 的读写与条目操作

    用法：
        db = ReferenceDatabase()
        db.load()
        db.add_entry(label="定音石", meta={"level": 100, "group": "调律材料"}, image_data=bgr_array)
        db.save()
    """

    def __init__(
        self,
        references_dir: Path | str | None = None,
        yaml_path: Path | str | None = None,
    ):
        self._dir = Path(references_dir) if references_dir else _DEFAULT_REFERENCES_DIR
        self._yaml_path = Path(yaml_path) if yaml_path else _DEFAULT_YAML_PATH
        self._entries: list[ReferenceEntry] = []
        self._meta_schema: list[MetaFieldDef] = []
        self._loaded = False

    # ─── 属性 ────────────────────────────────────────────

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def yaml_path(self) -> Path:
        return self._yaml_path

    @property
    def entries(self) -> list[ReferenceEntry]:
        self._ensure_loaded()
        return list(self._entries)

    # ─── 加载 / 保存 ─────────────────────────────────────

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    def load(self):
        """从 references.yaml 加载条目列表与 meta_schema"""
        if not self._yaml_path.exists():
            logger.info(f"references.yaml 不存在，使用空数据库: {self._yaml_path}")
            self._entries = []
            self._meta_schema = self._seed_schema()
            self._loaded = True
            return

        with open(self._yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            self._entries = []
            self._meta_schema = self._seed_schema()
            self._loaded = True
            return

        # 加载 meta_schema（缺失时种子化）
        self._meta_schema = self._parse_schema(data.get("meta_schema"))

        self._entries = []
        for item in data.get("references", []) or []:
            entry = ReferenceEntry(
                file=item.get("file", ""),
                label=item.get("label", ""),
                meta=item.get("meta", {}),
                source=item.get("source", ""),
                notes=item.get("notes", ""),
            )
            self._entries.append(entry)

        self._loaded = True
        logger.info(f"参考图库加载完成: {len(self._entries)} 条记录 <- {self._yaml_path}")

    @staticmethod
    def _seed_schema() -> list[MetaFieldDef]:
        """返回种子 meta_schema"""
        return [MetaFieldDef(**d) for d in _SEED_META_SCHEMA]

    def _parse_schema(self, raw) -> list[MetaFieldDef]:
        """解析 meta_schema 列表，缺失或为空时种子化"""
        if not raw:
            return self._seed_schema()
        # 种子字段索引：用于为旧 YAML 中未指定 type/sort_by 的字段提供默认值
        seed_by_key = {d["key"]: d for d in _SEED_META_SCHEMA}
        result: list[MetaFieldDef] = []
        for item in raw:
            key = item.get("key", "").strip()
            if not key:
                continue
            seed = seed_by_key.get(key, {})
            ftype = str(item.get("type", seed.get("type", "text")) or "text").strip().lower()
            if ftype not in ("text", "number"):
                ftype = "text"
            sort_by = str(item.get("sort_by", seed.get("sort_by", "asc")) or "asc").strip().lower()
            if sort_by not in ("asc", "desc"):
                sort_by = "asc"
            result.append(MetaFieldDef(
                key=key,
                name=item.get("name", "") or key,
                filterable=bool(item.get("filterable", False)),
                type=ftype,
                sort_by=sort_by,
            ))
        return result or self._seed_schema()

    def save(self):
        """保存条目列表与 meta_schema 到 references.yaml"""
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": 1,
            "meta_schema": [asdict(f) for f in self._meta_schema],
            "references": [asdict(e) for e in self._entries],
        }

        with open(self._yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"参考图库保存完成: {len(self._entries)} 条记录 -> {self._yaml_path}")

    # ─── 条目操作 ─────────────────────────────────────────

    def add_entry(
        self,
        label: str,
        meta: dict | None = None,
        source: str = "",
        notes: str = "",
        image_data: "np.ndarray | None" = None,
    ) -> ReferenceEntry:
        """新增参考图条目

        Args:
            label: 主标识（如 "定音石"）
            meta: 元数据字典（如 {"level": 100, "group": "调律材料"}）
            source: 来源截图名
            notes: 备注
            image_data: 如果提供，自动保存为 PNG 文件

        Returns:
            新建的 ReferenceEntry
        """
        group = (meta or {}).get("group", "")
        filename = self.next_filename(group)
        entry = ReferenceEntry(
            file=filename,
            label=label,
            meta=meta or {},
            source=source,
            notes=notes,
        )

        # 保存图片文件
        if image_data is not None:
            self._save_image(filename, image_data)

        self._entries.append(entry)
        self.save()
        logger.debug(f"新增参考图: {filename} label={label} meta={meta}")
        return entry

    def update_entry(self, filename: str, **kwargs) -> bool:
        """修改条目元数据

        如果 group 变更，会自动移动图片文件到新分组目录。

        Args:
            filename: 目标文件路径（相对路径）
            **kwargs: 要修改的字段（label, meta, source, notes）

        Returns:
            是否找到并修改
        """
        for entry in self._entries:
            if entry.file == filename:
                old_group = entry.group
                
                # 处理 meta 更新
                if "meta" in kwargs:
                    entry.meta.update(kwargs.pop("meta"))
                
                # 处理其他字段
                for key, value in kwargs.items():
                    if hasattr(entry, key) and key != "meta":
                        setattr(entry, key, value)

                # 如果 group 变更，移动图片文件
                new_group = entry.group
                if new_group != old_group:
                    self._move_image(filename, old_group, new_group)
                    # 更新 file 路径
                    old_name = Path(filename).name
                    entry.file = f"{new_group}/{old_name}" if new_group else old_name

                self.save()
                logger.debug(f"更新参考图: {filename} {kwargs}")
                return True
        logger.warning(f"未找到参考图文件: {filename}")
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
                        logger.debug(f"删除参考图文件: {file_path}")
                self.save()
                logger.debug(f"删除参考图条目: {filename}")
                return True
        return False

    # ─── 查询 ─────────────────────────────────────────────

    def list_entries(
        self,
        label_filter: str | None = None,
        group_filter: str | None = None,
        level_filter: int | None = None,
        meta_filters: dict[str, str] | None = None,
    ) -> list[ReferenceEntry]:
        """返回参考图条目列表

        Args:
            label_filter: 按标识过滤，None 返回全部
            group_filter: 按分组过滤，None 返回全部
            level_filter: 按等级过滤（兼容保留），None 返回全部
            meta_filters: 按任意 meta key 文本相等过滤（{key: 文本值}）
        """
        self._ensure_loaded()
        result = list(self._entries)
        if label_filter:
            result = [e for e in result if e.label == label_filter]
        if group_filter:
            result = [e for e in result if e.group == group_filter]
        if level_filter is not None:
            result = [e for e in result if e.level == level_filter]
        if meta_filters:
            for key, value in meta_filters.items():
                result = [e for e in result if self._meta_text(e, key) == value]
        return result

    def get_labels(self) -> list[str]:
        """返回所有去重标识列表（排序）"""
        self._ensure_loaded()
        labels = sorted({e.label for e in self._entries if e.label})
        return labels

    def get_labels_by_group(self, group: str) -> list[str]:
        """返回指定分组下的去重标识列表（排序）"""
        self._ensure_loaded()
        labels = sorted({e.label for e in self._entries if e.label and e.group == group})
        return labels

    def get_all_labels_by_group(self) -> dict[str, list[str]]:
        """返回所有分组 -> 标识列表的映射"""
        self._ensure_loaded()
        result: dict[str, set[str]] = {}
        for e in self._entries:
            if e.group and e.label:
                if e.group not in result:
                    result[e.group] = set()
                result[e.group].add(e.label)
        return {g: sorted(labels) for g, labels in result.items()}

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

    # ─── meta_schema 与通用 meta 查询 ──────────────────────

    def get_meta_schema(self) -> list[MetaFieldDef]:
        """返回 meta 字段定义列表"""
        self._ensure_loaded()
        return list(self._meta_schema)

    def set_meta_schema(self, schema: list[MetaFieldDef]):
        """设置并持久化 meta 字段定义"""
        self._ensure_loaded()
        self._meta_schema = list(schema)
        self.save()
        logger.debug(f"更新 meta_schema: {[f.key for f in schema]}")

    @staticmethod
    def _meta_text(entry: ReferenceEntry, key: str) -> str:
        """返回条目某 meta key 的文本值（统一 str，缺失为空）"""
        value = entry.meta.get(key)
        return "" if value is None else str(value)

    def get_meta_values(self, key: str) -> list[str]:
        """返回某 meta key 的去重文本值列表（升序）"""
        self._ensure_loaded()
        values = {self._meta_text(e, key) for e in self._entries}
        values.discard("")
        return sorted(values)

    def get_meta_options(self, field: MetaFieldDef) -> list[str]:
        """返回某 meta 字段的去重值列表，按 type/sort_by 排序

        - type="number"：按数值排序，数字优先于非数字文本，空值不在列表中
        - type="text"：按字典序升序
        """
        self._ensure_loaded()
        raw = {self._meta_text(e, field.key) for e in self._entries}
        raw.discard("")
        values = list(raw)
        if field.type == "number":
            def _num_key(v: str):
                try:
                    return (0, float(v))
                except (ValueError, TypeError):
                    return (1, v)
            values.sort(key=_num_key, reverse=(field.sort_by == "desc"))
        else:
            values.sort(reverse=(field.sort_by == "desc"))
        return values

    def get_by_label(self, label: str) -> list[ReferenceEntry]:
        """返回某标识下所有条目（含 file、meta）"""
        self._ensure_loaded()
        return [e for e in self._entries if e.label == label]

    def get_entry(self, filename: str) -> ReferenceEntry | None:
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
            logger.debug(f"保存参考图: {file_path} ({image_data.shape[1]}x{image_data.shape[0]})")
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
        logger.debug(f"移动参考图: {old_path} -> {new_path}")


# ─── 向后兼容别名（过渡期使用）────────────────────────────

# 旧名称映射，方便逐步迁移
MaterialEntry = ReferenceEntry
MaterialDatabase = ReferenceDatabase
