"""图像参考库 - 基于图库空间的参考图元数据管理

图库空间（space）：图库内部完全独立的配置集（各自拥有 meta_schema、
引用条目与参考图），用户选择激活一个空间，外部消费方无感。

参考图是配置资产，走 system/local 双层，每层均按空间组织：
- 名册：config/system/references.yaml（spaces 列表）
        + config/local/references.yaml（仅本地新建的空间）
- 作者层：config/system/references/{space}.yaml
          + config/system/references/{space}/{group}/*.png
- 用户层：config/local/references/{space}.yaml（条目级 diff：references + deleted）
          + config/local/references/{space}/{group}/*.png
- 激活空间：config/session/session.json 的 active_space 字段（纯运行态）

读取恒为合并视图：system 条目（deleted 剔除、同 file 被 local 条目替换）
+ local 独有条目；meta_schema 用户层存在即整列表替换。
写入按模式路由（开发→system，用户→local），与 ConfigResolver 同一套模式判定。
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from loguru import logger

from lvjiang.core.config_resolver import get_resolver

if TYPE_CHECKING:
    import numpy as np


# meta_schema 缺失时的种子字段（兼容现有数据：等级作为默认可筛选 meta 字段）
_SEED_META_SCHEMA = [
    {"key": "level", "name": "等级", "filterable": True, "type": "number", "sort_by": "asc"},
]

# 预制输入字段 key（图库内部使用，不参与业务展示）
_PREDEFINED_INPUT_KEYS = frozenset({"label", "group", "notes"})

# 默认图库空间名（名册缺失时的回退空间）
DEFAULT_SPACE = "默认"


def validate_crop(values: list[float]) -> list[float] | None:
    """校验 crop [x, y, w, h] 归一化坐标，非法返回 None"""
    if len(values) != 4:
        return None
    if any(v < 0.0 or v > 1.0 for v in values):
        return None
    x, y, w, h = values
    if x + w > 1.0 or y + h > 1.0:
        return None
    return values

# 匹配度阈值默认值（ORB+颜色综合置信度下限，yaml 未配置时使用）
# 实测真实匹配分布 0.46~0.67（n=893），0.35 留误拒余量同时拦纯颜色巧合
DEFAULT_MATCH_THRESHOLD = 0.35


@dataclass
class MetaFieldDef:
    """meta 字段定义

    Attributes:
        key: 内部标识（如 "level"）
        name: 显示名（如 "等级"）
        filterable: 是否在筛选栏展示
        type: 值类型，"text" 或 "number"
        sort_by: 排序方向，"asc" 或 "desc"
        scope: 元数据场景，"input"（用户填写、用于筛选管理）
            或 "output"（识别时按 crop 区域 OCR 产出）
        crop: output 字段专属的裁剪区域 [x, y, w, h]（归一化坐标），input 为 None
    """
    key: str
    name: str = ""
    filterable: bool = False
    type: str = "text"
    sort_by: str = "asc"
    scope: str = "input"
    crop: list[float] | None = None


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
    """图像参考库 - system/local 双层合并视图与按模式路由的读写

    用法：
        db = ReferenceDatabase()
        db.load()
        db.add_entry(label="定音石", meta={"level": 100, "group": "调律材料"}, image_data=bgr_array)
    """

    def __init__(
        self,
        system_dir: Path | str | None = None,
        system_yaml: Path | str | None = None,
        local_dir: Path | str | None = None,
        local_yaml: Path | str | None = None,
        dev_mode: bool | None = None,
        system_spaces_yaml: Path | str | None = None,
        local_spaces_yaml: Path | str | None = None,
        session_path: Path | str | None = None,
    ):
        self._system_dir_override = Path(system_dir) if system_dir else None
        self._system_yaml_override = Path(system_yaml) if system_yaml else None
        self._local_dir_override = Path(local_dir) if local_dir else None
        self._local_yaml_override = Path(local_yaml) if local_yaml else None
        self._dev_mode = dev_mode
        self._system_spaces_yaml_override = (
            Path(system_spaces_yaml) if system_spaces_yaml else None)
        self._local_spaces_yaml_override = (
            Path(local_spaces_yaml) if local_spaces_yaml else None)
        self._session_path_override = Path(session_path) if session_path else None
        # 空间状态
        self._spaces: list[str] = []
        self._space: str = DEFAULT_SPACE
        # 分层状态
        self._system_entries: list[ReferenceEntry] = []
        self._local_entries: list[ReferenceEntry] = []
        self._deleted: set[str] = set()
        self._system_schema: list[MetaFieldDef] = []
        self._local_schema: list[MetaFieldDef] | None = None
        self._system_threshold: float | None = None
        self._local_threshold: float | None = None
        # 合并视图（查询方法统一消费）
        self._entries: list[ReferenceEntry] = []
        self._meta_schema: list[MetaFieldDef] = []
        self._loaded = False

    # ─── 属性 ────────────────────────────────────────────

    @property
    def system_dir(self) -> Path:
        """激活空间的 system 层图片目录"""
        if self._system_dir_override is not None:
            return self._system_dir_override
        from lvjiang import constants
        return constants.SYSTEM_REFERENCES_DIR / self._space

    @property
    def local_dir(self) -> Path:
        """激活空间的 local 层图片目录"""
        if self._local_dir_override is not None:
            return self._local_dir_override
        from lvjiang import constants
        return constants.LOCAL_CONFIG_DIR / "references" / self._space

    @property
    def system_yaml_path(self) -> Path:
        """激活空间的 system 层配置 yaml"""
        if self._system_yaml_override is not None:
            return self._system_yaml_override
        from lvjiang import constants
        return constants.SYSTEM_REFERENCES_DIR / f"{self._space}.yaml"

    @property
    def local_yaml_path(self) -> Path:
        """激活空间的 local 层配置 yaml"""
        if self._local_yaml_override is not None:
            return self._local_yaml_override
        from lvjiang import constants
        return constants.LOCAL_CONFIG_DIR / "references" / f"{self._space}.yaml"

    @property
    def system_spaces_yaml_path(self) -> Path:
        """system 层空间名册 yaml"""
        if self._system_spaces_yaml_override is not None:
            return self._system_spaces_yaml_override
        from lvjiang import constants
        return constants.REFERENCES_CONFIG_PATH

    @property
    def local_spaces_yaml_path(self) -> Path:
        """local 层空间名册 yaml（仅本地新建的空间）"""
        if self._local_spaces_yaml_override is not None:
            return self._local_spaces_yaml_override
        from lvjiang import constants
        return constants.LOCAL_CONFIG_DIR / "references.yaml"

    @property
    def session_path(self) -> Path:
        """激活空间持久化位置（session.json）"""
        if self._session_path_override is not None:
            return self._session_path_override
        from lvjiang import constants
        return constants.SESSION_PATH

    @property
    def yaml_path(self) -> Path:
        """当前模式的写入目标 yaml（日志/展示用）"""
        return self.system_yaml_path if self._is_dev() else self.local_yaml_path

    def _is_dev(self) -> bool:
        if self._dev_mode is not None:
            return self._dev_mode
        return get_resolver().is_dev_mode()

    def image_path(self, rel_path: str) -> Path:
        """解析图片绝对路径：local 层优先 → system 层"""
        local = self.local_dir / rel_path
        if local.exists():
            return local
        return self.system_dir / rel_path

    @property
    def entries(self) -> list[ReferenceEntry]:
        self._ensure_loaded()
        return list(self._entries)

    # ─── 图库空间 ────────────────────────────────────

    @staticmethod
    def _parse_roster(path: Path) -> list[str]:
        """解析空间名册 yaml 的 spaces 列表，缺失/非法返回空列表"""
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"空间名册解析失败: {path}: {e}")
            return []
        spaces = data.get("spaces")
        if not isinstance(spaces, list):
            if data.get("references") is not None or data.get("meta_schema") is not None:
                logger.error(
                    f"检测到旧格式 references.yaml，请先运行迁移脚本 "
                    f".tooling/migrate_references_spaces.py: {path}"
                )
            return []
        return [str(s).strip() for s in spaces if str(s).strip()]

    def _read_session_active_space(self) -> str:
        """从 session.json 读取 active_space，缺失/非法返回空串"""
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(data.get("active_space") or "")

    def _write_session_active_space(self, name: str) -> None:
        """写 active_space 到 session.json（read-modify-write，保留其他字段）"""
        data: dict = {}
        if self.session_path.exists():
            try:
                data = json.loads(self.session_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["active_space"] = name
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _resolve_space(self) -> None:
        """解析激活空间：session.json → 名册首个 → DEFAULT_SPACE"""
        saved = self._read_session_active_space()
        if saved and saved in self._spaces:
            self._space = saved
        else:
            self._space = self._spaces[0] if self._spaces else DEFAULT_SPACE

    def get_spaces(self) -> list[str]:
        """空间列表（system 名册 ∪ local 名册，保序去重；名册全空回退 DEFAULT_SPACE）"""
        self._ensure_loaded()
        return list(self._spaces)

    def get_active_space(self) -> str:
        """当前激活的图库空间名"""
        self._ensure_loaded()
        return self._space

    def set_active_space(self, name: str) -> bool:
        """切换激活空间（持久化到 session.json）；非法名拒绝

        切换成功后自动 load() 重载新空间，避免旧空间内存态
        被后续 save() 写进新空间 yaml。
        """
        self._ensure_loaded()
        name = str(name or "").strip()
        if name not in self._spaces:
            logger.warning(f"图库空间不存在，无法切换: {name!r}")
            return False
        self._write_session_active_space(name)
        self._space = name
        self._loaded = False
        self.load()
        logger.info(f"图库空间已切换: {name}")
        return True

    def create_space(self, name: str) -> bool:
        """新建空图库空间（种子 meta_schema + 空条目）并注册名册

        空间 yaml 按模式写入可写层（dev→system，user→local）。
        """
        self._ensure_loaded()
        name = str(name or "").strip()
        if not name:
            logger.warning("图库空间名不能为空")
            return False
        if name in self._spaces:
            logger.warning(f"图库空间已存在: {name}")
            return False
        if self._is_dev():
            base = self.system_yaml_path.parent
            roster_path = self.system_spaces_yaml_path
        else:
            base = self.local_yaml_path.parent
            roster_path = self.local_spaces_yaml_path
        space_yaml = base / f"{name}.yaml"
        # 先注册名册（旧格式名册会被拒绝覆写，避免销毁未迁移的覆盖层数据）
        if not self._register_space(roster_path, name):
            return False
        if not space_yaml.exists():
            space_yaml.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "meta_schema": [],
                "references": [],
            }
            with open(space_yaml, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
        self._spaces.append(name)
        logger.info(f"新建图库空间: {name} -> {space_yaml}")
        return True

    @staticmethod
    def _register_space(roster_path: Path, name: str) -> bool:
        """将空间名追加到名册 yaml（保留已有名）；旧格式名册拒绝覆写

        local 层名册路径与旧版覆盖层文件同路径：未迁移的旧格式文件
        含用户定制条目与 deleted 墓碑，覆写会造成数据丢失，必须先跑迁移脚本。
        """
        spaces: list[str] = []
        if roster_path.exists():
            try:
                with open(roster_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                data = {}
            raw = data.get("spaces")
            if isinstance(raw, list):
                spaces = [str(s).strip() for s in raw if str(s).strip()]
            elif data.get("references") is not None or data.get("meta_schema") is not None:
                logger.error(
                    f"检测到旧格式名册，拒绝覆写，请先运行迁移脚本 "
                    f".tooling/migrate_references_spaces.py: {roster_path}"
                )
                return False
        if name in spaces:
            return True
        spaces.append(name)
        roster_path.parent.mkdir(parents=True, exist_ok=True)
        with open(roster_path, "w", encoding="utf-8") as f:
            yaml.dump({"version": 1, "spaces": spaces}, f,
                      allow_unicode=True, default_flow_style=False, sort_keys=False)
        return True

    # ─── 加载 / 保存 ─────────────────────────────────────

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    @staticmethod
    def _parse_entries(raw_list) -> list[ReferenceEntry]:
        entries = []
        for item in raw_list or []:
            entries.append(ReferenceEntry(
                file=item.get("file", ""),
                label=item.get("label", ""),
                meta=item.get("meta", {}),
                source=item.get("source", ""),
                notes=item.get("notes", ""),
            ))
        return entries

    def load(self):
        """加载空间名册 + 激活空间的 system 层与 local 覆盖层，重建合并视图"""
        # 空间名册与激活空间（路径属性依赖 self._space，必须最先解析）
        system_spaces = self._parse_roster(self.system_spaces_yaml_path)
        local_spaces = self._parse_roster(self.local_spaces_yaml_path)
        merged_spaces = list(system_spaces)
        merged_spaces.extend(s for s in local_spaces if s not in merged_spaces)
        self._spaces = merged_spaces or [DEFAULT_SPACE]
        self._resolve_space()

        # system 层
        if self.system_yaml_path.exists():
            with open(self.system_yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._system_entries = self._parse_entries(data.get("references"))
            self._system_schema = self._parse_schema(data.get("meta_schema"))
            self._system_threshold = self._parse_threshold(data.get("match_threshold"))
        else:
            logger.info(f"references.yaml 不存在，使用空数据库: {self.system_yaml_path}")
            self._system_entries = []
            self._system_schema = self._seed_schema()
            self._system_threshold = None

        # local 覆盖层（条目级 diff：references + deleted + 可选 meta_schema）
        self._local_entries = []
        self._deleted = set()
        self._local_schema = None
        self._local_threshold = None
        if self.local_yaml_path.exists():
            with open(self.local_yaml_path, "r", encoding="utf-8") as f:
                overlay = yaml.safe_load(f) or {}
            self._local_entries = self._parse_entries(overlay.get("references"))
            self._deleted = {str(x) for x in overlay.get("deleted") or []}
            if overlay.get("meta_schema"):
                self._local_schema = self._parse_schema(overlay["meta_schema"])
            self._local_threshold = self._parse_threshold(overlay.get("match_threshold"))

        self._rebuild_merged()
        self._loaded = True
        logger.info(
            f"参考图库加载完成（空间={self._space}）: 合并 {len(self._entries)} 条"
            f"（system {len(self._system_entries)} / local {len(self._local_entries)}"
            f" / 删除 {len(self._deleted)}）"
        )

    def _rebuild_merged(self):
        """重建合并视图：system（剔除 deleted、同 file 被 local 替换）+ local 独有"""
        local_by_file = {e.file: e for e in self._local_entries}
        merged: list[ReferenceEntry] = []
        for e in self._system_entries:
            if e.file in self._deleted:
                continue
            merged.append(local_by_file.pop(e.file, e))
        # local 独有条目按原顺序追加
        merged.extend(e for e in self._local_entries if e.file in local_by_file)
        self._entries = merged
        self._meta_schema = (self._local_schema
                             if self._local_schema is not None
                             else self._system_schema)

    @staticmethod
    def _seed_schema() -> list[MetaFieldDef]:
        """返回种子 meta_schema"""
        return [MetaFieldDef(**d) for d in _SEED_META_SCHEMA]

    @staticmethod
    def _parse_threshold(raw) -> float | None:
        """解析 match_threshold，非法/缺失返回 None（落回下层或默认值）"""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None

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
                scope=self._parse_scope(item.get("scope")),
                crop=self._parse_crop(item.get("crop")),
            ))
        return result or self._seed_schema()

    @staticmethod
    def _parse_scope(raw) -> str:
        """解析 scope，非法/缺失默认 input"""
        scope = str(raw or "input").strip().lower()
        return scope if scope in ("input", "output") else "input"

    @staticmethod
    def _parse_crop(raw) -> list[float] | None:
        """解析 crop 区域 [x, y, w, h]（归一化），非法返回 None"""
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            values = [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
        return validate_crop(values)

    def save(self):
        """按模式落盘：开发→system 全量；用户→local 条目级 diff（空则删覆盖文件）"""
        self._ensure_loaded()
        if self._is_dev():
            self.system_yaml_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "meta_schema": [self._schema_to_dict(f) for f in self._system_schema],
                "references": [asdict(e) for e in self._system_entries],
            }
            if self._system_threshold is not None:
                data["match_threshold"] = self._system_threshold
            with open(self.system_yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.info(f"参考图库保存完成: {len(self._system_entries)} 条记录 -> {self.system_yaml_path}")
            return

        has_overlay = bool(self._local_entries or self._deleted
                           or self._local_schema is not None
                           or self._local_threshold is not None)
        if not has_overlay:
            if self.local_yaml_path.exists():
                self.local_yaml_path.unlink()
            return
        overlay: dict = {
            "version": 1,
            "references": [asdict(e) for e in self._local_entries],
            "deleted": sorted(self._deleted),
        }
        if self._local_schema is not None:
            overlay["meta_schema"] = [self._schema_to_dict(f) for f in self._local_schema]
        if self._local_threshold is not None:
            overlay["match_threshold"] = self._local_threshold
        self.local_yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.local_yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(overlay, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(
            f"参考图库覆盖层保存完成: {len(self._local_entries)} 条"
            f" + 删除 {len(self._deleted)} -> {self.local_yaml_path}"
        )

    @staticmethod
    def _schema_to_dict(f: MetaFieldDef) -> dict:
        """序列化字段定义：crop 为 None 时省略该键"""
        d = asdict(f)
        if d.get("crop") is None:
            d.pop("crop", None)
        return d

    @staticmethod
    def _find(entries: list[ReferenceEntry], filename: str) -> "ReferenceEntry | None":
        for e in entries:
            if e.file == filename:
                return e
        return None

    # ─── 条目操作 ─────────────────────────────────────────

    def add_entry(
        self,
        label: str,
        meta: dict | None = None,
        source: str = "",
        notes: str = "",
        image_data: "np.ndarray | None" = None,
    ) -> ReferenceEntry:
        """新增参考图条目（开发→system 层，用户→local 层，图片与条目同层）

        Args:
            label: 主标识（如 "定音石"）
            meta: 元数据字典（如 {"level": 100, "group": "调律材料"}）
            source: 来源截图名
            notes: 备注
            image_data: 如果提供，自动保存为 PNG 文件

        Returns:
            新建的 ReferenceEntry
        """
        self._ensure_loaded()
        group = (meta or {}).get("group", "")
        filename = self.next_filename(group)
        entry = ReferenceEntry(
            file=filename,
            label=label,
            meta=meta or {},
            source=source,
            notes=notes,
        )

        # 保存图片文件（与条目同层）
        if image_data is not None:
            layer_dir = self.system_dir if self._is_dev() else self.local_dir
            self._save_image(layer_dir, filename, image_data)

        target = self._system_entries if self._is_dev() else self._local_entries
        target.append(entry)
        self.save()
        self._rebuild_merged()
        logger.debug(f"新增参考图: {filename} label={label} meta={meta}")
        return entry

    def update_entry(self, filename: str, **kwargs) -> bool:
        """修改条目元数据

        group 变更时只搬当前可写层实际存在的图片（搬动成功才改 file 路径）；
        用户模式改 system 条目时复制进 local 做影子（file 不变，图片不动）。

        Args:
            filename: 目标文件路径（相对路径）
            **kwargs: 要修改的字段（label, meta, source, notes）

        Returns:
            是否找到并修改
        """
        self._ensure_loaded()
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            logger.warning(f"未找到参考图文件: {filename}")
            return False

        if local_entry is not None:
            entry = local_entry
            layer_dir = self.local_dir
        elif self._is_dev():
            entry = system_entry
            layer_dir = self.system_dir
        else:
            # 用户模式改 system 条目 → 复制进 local 做影子
            entry = ReferenceEntry(**asdict(system_entry))
            self._local_entries.append(entry)
            layer_dir = None  # 图片在 system 层，不动

        old_group = entry.group
        if "meta" in kwargs:
            entry.meta.update(kwargs.pop("meta"))
        for key, value in kwargs.items():
            if hasattr(entry, key) and key != "meta":
                setattr(entry, key, value)

        new_group = entry.group
        if new_group != old_group and layer_dir is not None:
            if self._move_image(layer_dir, entry.file, new_group):
                old_name = Path(entry.file).name
                entry.file = f"{new_group}/{old_name}" if new_group else old_name

        self.save()
        self._rebuild_merged()
        logger.debug(f"更新参考图: {filename} {kwargs}")
        return True

    def remove_entry(self, filename: str, delete_file: bool = True) -> bool:
        """删除条目

        开发模式直删所在层；用户模式下 local 独有条目删条目删图，
        system 条目落 deleted 列表（system 图片不动）。

        Args:
            filename: 目标文件路径（相对路径）
            delete_file: 是否同时删除可写层的图片文件

        Returns:
            是否找到并删除
        """
        self._ensure_loaded()
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            return False

        if local_entry is not None:
            self._local_entries.remove(local_entry)
            if delete_file:
                self._delete_image(self.local_dir, filename)
        if system_entry is not None:
            if self._is_dev():
                self._system_entries.remove(system_entry)
                if delete_file:
                    self._delete_image(self.system_dir, filename)
            else:
                self._deleted.add(filename)

        self.save()
        self._rebuild_merged()
        logger.debug(f"删除参考图条目: {filename}")
        return True

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
        """设置并持久化 meta 字段定义（用户模式整列表替换进 local）"""
        self._ensure_loaded()
        if self._is_dev():
            self._system_schema = list(schema)
        else:
            self._local_schema = list(schema)
        self.save()
        self._rebuild_merged()
        logger.debug(f"更新 meta_schema: {[f.key for f in schema]}")

    def get_output_fields(self) -> list[MetaFieldDef]:
        """返回输出元数据字段（scope=output 且 crop 有效），按定义顺序"""
        self._ensure_loaded()
        return [f for f in self._meta_schema
                if f.scope == "output" and f.crop is not None]

    def get_custom_input_fields(self) -> list[MetaFieldDef]:
        """返回非预制的输入元数据字段（排除 label/group/notes）"""
        self._ensure_loaded()
        return [f for f in self._meta_schema
                if f.scope == "input" and f.key not in _PREDEFINED_INPUT_KEYS]

    # ─── 匹配度阈值 ───────────────────────────────────

    def get_match_threshold(self) -> float:
        """返回匹配度阈值：local 覆盖 → system → 默认值"""
        self._ensure_loaded()
        if self._local_threshold is not None:
            return self._local_threshold
        if self._system_threshold is not None:
            return self._system_threshold
        return DEFAULT_MATCH_THRESHOLD

    def set_match_threshold(self, value: float):
        """设置并持久化匹配度阈值（开发→system；用户→local 覆盖，
        回设为 system 有效值时清除覆盖）"""
        self._ensure_loaded()
        value = round(float(value), 4)
        if self._is_dev():
            self._system_threshold = value
        else:
            system_effective = (self._system_threshold
                                if self._system_threshold is not None
                                else DEFAULT_MATCH_THRESHOLD)
            self._local_threshold = None if value == system_effective else value
        self.save()
        logger.debug(f"更新匹配度阈值: {value}")

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

    def _save_image(self, layer_dir: Path, rel_path: str, image_data: "np.ndarray"):
        """保存 numpy 图像到指定层目录（按相对路径，支持中文路径）"""
        import cv2
        file_path = layer_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite 不支持中文路径，用 imencode + 文件写入
        success, buf = cv2.imencode('.png', image_data)
        if success:
            file_path.write_bytes(buf.tobytes())
            logger.debug(f"保存参考图: {file_path} ({image_data.shape[1]}x{image_data.shape[0]})")
        else:
            logger.error(f"图片编码失败: {file_path}")

    @staticmethod
    def _delete_image(layer_dir: Path, rel_path: str):
        """删除指定层的图片文件（不存在则静默跳过）"""
        file_path = layer_dir / rel_path
        if file_path.exists():
            file_path.unlink()
            logger.debug(f"删除参考图文件: {file_path}")

    def _move_image(self, layer_dir: Path, rel_path: str, new_group: str) -> bool:
        """在指定层内移动图片到新分组目录

        Returns:
            是否实际发生移动（图片不在该层时返回 False，调用方据此决定是否改 file 路径）
        """
        old_path = layer_dir / rel_path
        if not old_path.exists():
            return False

        name = old_path.name
        new_dir = layer_dir / new_group if new_group else layer_dir
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / name

        old_path.rename(new_path)
        logger.debug(f"移动参考图: {old_path} -> {new_path}")
        return True


# ─── 向后兼容别名（过渡期使用）────────────────────────────

# 旧名称映射，方便逐步迁移
MaterialEntry = ReferenceEntry
MaterialDatabase = ReferenceDatabase
