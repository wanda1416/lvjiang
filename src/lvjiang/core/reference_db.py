"""图像参考库 - 基于图库空间的参考图元数据管理

图库空间（space）：图库内部完全独立的配置集（各自拥有 meta_schema、
引用条目与参考图），用户选择激活一个空间，外部消费方无感。

参考图是配置资产，走 system/local 双层，每层均按空间组织：
- 空间发现：扫描 config/system/references/*.yaml 与 config/local/references/*.yaml
        取文件名为空间名，system ∪ local 去重（无独立名册文件）
- 作者层：config/system/references/{space}.yaml
          + config/system/references/{space}/{bucket}/*.png
- 用户层：config/local/references/{space}.yaml（条目级 diff：references + deleted）
          + config/local/references/{space}/{bucket}/*.png
- 激活空间：config/session/session.json 的 active_space 字段（纯运行态）

桶（bucket）：每个空间的桶目录由代码扫描空间目录下的全部二级子目录自动发现。
文件随机分配到某个桶，YAML 中 file 字段只存文件名（不含桶路径）。
用户可自由移动文件到任意桶，代码均能感知。

system 层出现过的空间名即出厂空间：用户层只能覆盖其内容，不能删除该空间。
读取恒为合并视图：system 条目（deleted 剔除、同 file 被 local 条目替换）
+ local 独有条目；meta_schema 用户层存在即整列表替换。
写入按模式路由（开发→system，用户→local），与 ConfigResolver 同一套模式判定。
"""

import random
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.core.config import load_yaml, save_yaml
from lvjiang.core.config.resolver import get_resolver

from ..i18n import tr

if TYPE_CHECKING:
    import numpy as np


# meta_schema 缺失时的种子字段（兼容现有数据：等级作为默认可筛选 meta 字段）
_SEED_META_SCHEMA = [
    {"key": "level", "name": tr("等级"), "filterable": True, "type": "number", "sort_by": "asc"},
]

# 预制输入字段 key（图库内部使用，不参与业务展示）
_PREDEFINED_INPUT_KEYS = frozenset({"label", "group", "notes"})

# rich reference dict 的引擎保留字段；output OCR 字段会被摊平，不能占用。
REFERENCE_OUTPUT_RESERVED_KEYS = frozenset({"label", "group", "confidence", "meta"})

# 默认图库空间名（名册缺失时的回退空间）
DEFAULT_SPACE = tr("默认")


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
        file: 图片文件名（如 "A1B2C3D4.png"，不含桶目录路径）
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
        """元数据中的分组字段（逗号分隔的多组字符串）"""
        return self.meta.get("group", "")

    @group.setter
    def group(self, value: str):
        self.meta["group"] = value

    @property
    def groups(self) -> list[str]:
        """元数据中的分组列表（支持逗号分隔多组）"""
        raw = self.meta.get("group", "")
        if not raw:
            return []
        return [g.strip() for g in str(raw).split(",") if g.strip()]

    @groups.setter
    def groups(self, value: list[str]):
        self.meta["group"] = ",".join(g.strip() for g in value if g.strip())

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
        system_spaces_dir: Path | str | None = None,
        local_spaces_dir: Path | str | None = None,
        session_path: Path | str | None = None,
    ):
        self._system_dir_override = Path(system_dir) if system_dir else None
        self._system_yaml_override = Path(system_yaml) if system_yaml else None
        self._local_dir_override = Path(local_dir) if local_dir else None
        self._local_yaml_override = Path(local_yaml) if local_yaml else None
        self._dev_mode = dev_mode
        self._system_spaces_dir_override = (
            Path(system_spaces_dir) if system_spaces_dir else None)
        self._local_spaces_dir_override = (
            Path(local_spaces_dir) if local_spaces_dir else None)
        self._session_path_override = Path(session_path) if session_path else None
        self._session_store_cache = None  # 仅 session_path override 时使用的独立 store
        # 空间状态
        self._spaces: list[str] = []
        self._system_spaces: set[str] = set()  # system 层扫出的出厂空间
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
        self._buckets: list[str] = []  # 目录扫描发现的桶
        self.load()  # 构造时立即加载

    # ─── 属性 ────────────────────────────────────────────

    @property
    def system_dir(self) -> Path:
        """激活空间的 system 层图片目录"""
        if self._system_dir_override is not None:
            return self._system_dir_override
        return get_resolver().system_dir / "references" / self._space

    @property
    def local_dir(self) -> Path:
        """激活空间的 local 层图片目录"""
        if self._local_dir_override is not None:
            return self._local_dir_override
        return get_resolver().local_dir / "references" / self._space

    @property
    def system_yaml_path(self) -> Path:
        """激活空间的 system 层配置 yaml"""
        if self._system_yaml_override is not None:
            return self._system_yaml_override
        return get_resolver().system_dir / "references" / f"{self._space}.yaml"

    @property
    def local_yaml_path(self) -> Path:
        """激活空间的 local 层配置 yaml"""
        if self._local_yaml_override is not None:
            return self._local_yaml_override
        return get_resolver().local_dir / "references" / f"{self._space}.yaml"

    @property
    def system_spaces_dir(self) -> Path:
        """system 层空间根目录（扫描 *.yaml 得出厂空间列表）"""
        if self._system_spaces_dir_override is not None:
            return self._system_spaces_dir_override
        return get_resolver().system_dir / "references"

    @property
    def local_spaces_dir(self) -> Path:
        """local 层空间根目录（覆盖层 yaml + 本地新建空间 yaml）"""
        if self._local_spaces_dir_override is not None:
            return self._local_spaces_dir_override
        return get_resolver().local_dir / "references"

    @property
    def session_path(self) -> Path:
        """激活空间持久化位置（session.json）"""
        if self._session_path_override is not None:
            return self._session_path_override
        from lvjiang import constants
        return constants.SESSION_PATH


    def _is_dev(self) -> bool:
        if self._dev_mode is not None:
            return self._dev_mode
        return get_resolver().is_dev_mode()

    def image_path(self, filename: str) -> Path:
        """解析图片绝对路径：遍历全部桶目录查找文件

        Args:
            filename: 文件名（不含桶路径，如 'A1B2C3D4.png'）

        Returns:
            文件绝对路径（local 层优先 → system 层），不存在时返回 system 层路径
        """
        # 先在 local 层的全部桶中查找
        for bucket in self._buckets:
            local = self.local_dir / bucket / filename
            if local.exists():
                return local
        # 再在 system 层的全部桶中查找
        for bucket in self._buckets:
            system = self.system_dir / bucket / filename
            if system.exists():
                return system
        # 未找到时返回 system 层第一个桶的路径（保持向后兼容）
        if self._buckets:
            return self.system_dir / self._buckets[0] / filename
        return self.system_dir / filename

    @property
    def buckets(self) -> list[str]:
        """当前空间的桶列表（目录扫描发现）"""
        return list(self._buckets)

    @property
    def entries(self) -> list[ReferenceEntry]:
        return list(self._entries)

    # ─── 图库空间 ────────────────────────────────────

    @staticmethod
    def _scan_spaces(spaces_dir: Path) -> list[str]:
        """扫描空间根目录下的 *.yaml，文件名即空间名（按名排序）

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
    def _warn_legacy_roster(layer_dir: Path) -> None:
        """旧版 references.yaml 已作废：存在即提示，内容一律忽略"""
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
                f"请手动改名为 references/{DEFAULT_SPACE}.yaml 以保留数据"
            )
        else:
            logger.info(f"空间名册已作废（空间改为扫描目录得出），可删除: {legacy}")

    def _get_session_store(self):
        """session.json 读写入口：缺省路径用全局单例，override 时用独立实例（测试隔离）"""
        if self._session_path_override is not None:
            if self._session_store_cache is None:
                from lvjiang.core.config.session import SessionStore
                self._session_store_cache = SessionStore(self._session_path_override)
            return self._session_store_cache
        from lvjiang.core.config.session import get_session_store
        return get_session_store()

    def _read_session_active_space(self) -> str:
        """从 session.json 读取 active_space，缺失/非法返回空串"""
        value = self._get_session_store().get_node("active_space")
        return str(value) if value else ""

    def _write_session_active_space(self, name: str) -> None:
        """写 active_space 到 session.json（经 SessionStore，不影响其他节点）"""
        self._get_session_store().set_node("active_space", name)

    def _resolve_space(self) -> None:
        """解析激活空间：session.json → DEFAULT_SPACE → 首个 → DEFAULT_SPACE"""
        saved = self._read_session_active_space()
        if saved and saved in self._spaces:
            self._space = saved
        elif DEFAULT_SPACE in self._spaces:
            self._space = DEFAULT_SPACE
        else:
            self._space = self._spaces[0] if self._spaces else DEFAULT_SPACE

    def get_spaces(self) -> list[str]:
        """空间列表（system 扫描 ∪ local 扫描，各自按名排序；全空回退 DEFAULT_SPACE）"""
        return list(self._spaces)

    def is_system_space(self, name: str) -> bool:
        """该空间是否由 system 层定义（出厂内容，用户层只能覆盖不能删除）"""
        return str(name or "").strip() in self._system_spaces

    def is_user_mode(self) -> bool:
        """当前是否为用户模式（写 local 层；出厂内容只能覆盖不能删除）"""
        return not self._is_dev()

    def get_active_space(self) -> str:
        """当前激活的图库空间名"""
        return self._space

    def set_active_space(self, name: str) -> bool:
        """切换激活空间（持久化到 session.json）；非法名拒绝

        切换成功后自动 load() 重载新空间，避免旧空间内存态
        被后续 save() 写进新空间 yaml。
        """
        name = str(name or "").strip()
        if name not in self._spaces:
            logger.warning(f"图库空间不存在，无法切换: {name!r}")
            return False
        self._write_session_active_space(name)
        self._space = name
        self.load()  # 重载新空间
        logger.info(f"图库空间已切换: {name}")
        return True

    def create_space(self, name: str) -> bool:
        """新建空图库空间（空 meta_schema + 空条目）

        空间 yaml 按模式写入可写层（dev→system，user→local），
        落盘即注册——空间列表由目录扫描得出。
        """
        name = str(name or "").strip()
        if not self._valid_space_name(name):
            return False
        if name in self._spaces:
            logger.warning(f"图库空间已存在: {name}")
            return False
        base = self.system_spaces_dir if self._is_dev() else self.local_spaces_dir
        space_yaml = base / f"{name}.yaml"
        if not space_yaml.exists():
            save_yaml(space_yaml, {
                "version": 1,
                "meta_schema": [],
                "references": [],
            })
        self._refresh_spaces()
        logger.info(f"新建图库空间: {name} -> {space_yaml}")
        return True

    def can_delete_space(self, name: str) -> str:
        """返回该空间**不可**删除的原因；可删则返回空串

        UI 用它决定按钮禁用与提示文案，delete_space 用它做最终把关。
        """
        name = str(name or "").strip()
        if name not in self._spaces:
            return tr("空间不存在")
        if self.is_user_mode() and self.is_system_space(name):
            return tr("出厂空间不可删除，可新建自己的空间")
        if len(self._spaces) <= 1:
            return tr("至少保留一个图库空间")
        return ""

    def delete_space(self, name: str) -> bool:
        """删除图库空间（yaml + 同名图片目录）

        用户模式只清 local 层且拒绝出厂空间；开发模式两层一起清，
        避免 system 层删掉后残留 local 覆盖层变成一个孤儿空间。
        删的是激活空间时自动改激活并重载。
        """
        name = str(name or "").strip()
        if not self._valid_space_name(name):
            return False
        reason = self.can_delete_space(name)
        if reason:
            logger.warning(f"拒绝删除图库空间 {name!r}: {reason}")
            return False
        layers = [self.local_spaces_dir]
        if self._is_dev():
            layers.append(self.system_spaces_dir)
        for layer in layers:
            self._remove_space_files(layer, name)
        self._refresh_spaces()
        if self._space == name:  # 激活空间被删：改激活并重载
            self._resolve_space()
            self._write_session_active_space(self._space)
            self.load()
        logger.info(f"已删除图库空间: {name}（当前激活={self._space}）")
        return True

    @staticmethod
    def _remove_space_files(spaces_dir: Path, name: str) -> None:
        """删除某一层的空间 yaml 与同名图片目录（不存在则跳过）"""
        space_yaml = spaces_dir / f"{name}.yaml"
        if space_yaml.is_file():
            space_yaml.unlink()
            logger.info(f"删除空间 yaml: {space_yaml}")
        image_dir = spaces_dir / name
        if image_dir.is_dir():
            shutil.rmtree(image_dir)
            logger.info(f"删除空间图片目录: {image_dir}")

    @staticmethod
    def _valid_space_name(name: str) -> bool:
        """空间名即文件名：拒空、拒路径分隔符、拒 . 开头（隐藏文件）"""
        if not name:
            logger.warning("图库空间名不能为空")
            return False
        if name.startswith(".") or set(name) & set('/\\:*?"<>|'):
            logger.warning(f"图库空间名非法（不能含路径分隔符或以 . 开头）: {name!r}")
            return False
        return True

    def _refresh_spaces(self) -> None:
        """重扫两层空间根目录，重建空间列表与出厂空间集合"""
        system_spaces = self._scan_spaces(self.system_spaces_dir)
        local_spaces = self._scan_spaces(self.local_spaces_dir)
        self._system_spaces = set(system_spaces)
        merged = list(system_spaces)
        merged.extend(s for s in local_spaces if s not in self._system_spaces)
        self._spaces = merged or [DEFAULT_SPACE]

    # ─── 加载 / 保存 ─────────────────────────────────────

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
        """扫描空间列表 + 加载激活空间的 system 层与 local 覆盖层，重建合并视图"""
        # 空间列表与激活空间（路径属性依赖 self._space，必须最先解析）
        self._refresh_spaces()
        if self._local_spaces_dir_override is None:  # override 场景（测试）不体检真实 config
            self._warn_legacy_roster(get_resolver().local_dir)
        self._resolve_space()

        # system 层
        if self.system_yaml_path.exists():
            data = load_yaml(self.system_yaml_path)
            self._system_entries = self._parse_entries(data.get("references"))
            self._system_schema = self._parse_schema(data.get("meta_schema"))
            self._system_threshold = self._parse_threshold(data.get("match_threshold"))
        else:
            logger.info(f"空间 yaml 不存在，使用空数据库: {self.system_yaml_path}")
            self._system_entries = []
            self._system_schema = self._seed_schema()
            self._system_threshold = None

        # local 覆盖层（条目级 diff：references + deleted + 可选 meta_schema）
        self._local_entries = []
        self._deleted = set()
        self._local_schema = None
        self._local_threshold = None
        if self.local_yaml_path.exists():
            overlay = load_yaml(self.local_yaml_path)
            self._local_entries = self._parse_entries(overlay.get("references"))
            self._deleted = {str(x) for x in overlay.get("deleted") or []}
            if overlay.get("meta_schema"):
                self._local_schema = self._parse_schema(overlay["meta_schema"])
            self._local_threshold = self._parse_threshold(overlay.get("match_threshold"))

        self._rebuild_merged()
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
        # 桶：目录扫描发现（local 层 + system 层合并去重）
        self._buckets = self._discover_buckets()

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

    def _discover_buckets(self) -> list[str]:
        """扫描 local 层 + system 层目录，发现全部二级子目录作为桶"""
        buckets: list[str] = []
        # 先扫描 local 层
        if self.local_dir.exists():
            for d in self.local_dir.iterdir():
                if d.is_dir() and not d.name.startswith('.') and d.name not in buckets:
                    buckets.append(d.name)
        # 再扫描 system 层
        if self.system_dir.exists():
            for d in self.system_dir.iterdir():
                if d.is_dir() and not d.name.startswith('.') and d.name not in buckets:
                    buckets.append(d.name)
        return sorted(buckets)

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
            if (
                result[-1].scope == "output"
                and result[-1].key in REFERENCE_OUTPUT_RESERVED_KEYS
            ):
                raise ValueError(
                    f"output 元数据字段 {key!r} 是参考图识别保留字段"
                )
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
        if self._is_dev():
            data = {
                "version": 1,
                "meta_schema": [self._schema_to_dict(f) for f in self._system_schema],
                "references": [asdict(e) for e in self._system_entries],
            }
            if self._system_threshold is not None:
                data["match_threshold"] = self._system_threshold
            save_yaml(self.system_yaml_path, data)
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
        save_yaml(self.local_yaml_path, overlay)
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
        """新增参考图条目（开发→system 层，用户→local 层，图片随机分配到桶）

        Args:
            label: 主标识（如 "定音石"）
            meta: 元数据字典（如 {"level": 100, "group": "调律材料,装备培养"}）
            source: 来源截图名
            notes: 备注
            image_data: 如果提供，自动保存为 PNG 文件（随机分配到注册桶）

        Returns:
            新建的 ReferenceEntry
        """
        filename = self.next_filename()
        entry = ReferenceEntry(
            file=filename,
            label=label,
            meta=meta or {},
            source=source,
            notes=notes,
        )

        # 保存图片文件（随机分配到桶）
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

        group 变更不再移动文件（文件与分组解耦，用户可自由移动文件到任意桶）。
        用户模式改 system 条目时复制进 local 做影子（file 不变，图片不动）。

        Args:
            filename: 目标文件名（不含桶路径）
            **kwargs: 要修改的字段（label, meta, source, notes）

        Returns:
            是否找到并修改
        """
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            logger.warning(f"未找到参考图文件: {filename}")
            return False

        if local_entry is not None:
            entry = local_entry
        elif self._is_dev():
            entry = system_entry
        else:
            # 用户模式改 system 条目 → 复制进 local 做影子
            entry = ReferenceEntry(**asdict(system_entry))
            self._local_entries.append(entry)

        if "meta" in kwargs:
            entry.meta.update(kwargs.pop("meta"))
        for key, value in kwargs.items():
            if hasattr(entry, key) and key != "meta":
                setattr(entry, key, value)

        self.save()
        self._rebuild_merged()
        logger.debug(f"更新参考图: {filename} {kwargs}")
        return True

    def remove_entry(self, filename: str, delete_file: bool = True) -> bool:
        """删除条目

        开发模式直删所在层。用户模式下只能删自己的 local 条目；出厂条目属于
        system 内容，不允许删除——想要一套自己的图请**新建图库空间**，
        而不是把出厂图去掉（详见 docs/30-architecture/05-config-layering.md）。

        Args:
            filename: 目标文件路径（相对路径）
            delete_file: 是否同时删除可写层的图片文件

        Raises:
            SystemContentProtected: 用户模式下试图删除出厂条目

        Returns:
            是否找到并删除
        """
        local_entry = self._find(self._local_entries, filename)
        system_entry = self._find(self._system_entries, filename)
        if local_entry is None and system_entry is None:
            return False

        if system_entry is not None and not self._is_dev():
            from .config.resolver import SystemContentProtected
            raise SystemContentProtected(
                f"参考图 {filename} 由出厂图库提供，用户模式下不可删除；"
                f"如需自己的一套请新建图库空间")

        if local_entry is not None:
            self._local_entries.remove(local_entry)
            if delete_file:
                self._delete_image(self.local_dir, filename)
        if system_entry is not None:
            self._system_entries.remove(system_entry)
            if delete_file:
                self._delete_image(self.system_dir, filename)

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
        result = list(self._entries)
        if label_filter:
            result = [e for e in result if e.label == label_filter]
        if group_filter:
            result = [e for e in result if group_filter in e.groups]
        if level_filter is not None:
            result = [e for e in result if e.level == level_filter]
        if meta_filters:
            for key, value in meta_filters.items():
                result = [e for e in result if self._meta_text(e, key) == value]
        return result

    def get_labels(self) -> list[str]:
        """返回所有去重标识列表（排序）"""
        labels = sorted({e.label for e in self._entries if e.label})
        return labels


    def get_all_labels_by_group(self) -> dict[str, list[str]]:
        """返回所有分组 -> 标识列表的映射，支持逗号分隔的多组"""
        result: dict[str, set[str]] = {}
        for e in self._entries:
            if e.label:
                for group in e.groups:
                    if group not in result:
                        result[group] = set()
                    result[group].add(e.label)
        return {g: sorted(labels) for g, labels in result.items()}

    def get_groups(self) -> list[str]:
        """返回所有去重分组列表（排序），支持逗号分隔的多组"""
        all_groups: set[str] = set()
        for e in self._entries:
            all_groups.update(e.groups)
        return sorted(all_groups)


    # ─── meta_schema 与通用 meta 查询 ──────────────────────

    def get_meta_schema(self) -> list[MetaFieldDef]:
        """返回 meta 字段定义列表"""
        return list(self._meta_schema)

    def set_meta_schema(self, schema: list[MetaFieldDef]):
        """设置并持久化 meta 字段定义（用户模式整列表替换进 local）"""
        conflicts = sorted(
            field.key for field in schema
            if field.scope == "output"
            and field.key in REFERENCE_OUTPUT_RESERVED_KEYS
        )
        if conflicts:
            raise ValueError(
                f"output 元数据字段占用参考图识别保留字段: {', '.join(conflicts)}"
            )
        if self._is_dev():
            self._system_schema = list(schema)
        else:
            self._local_schema = list(schema)
        self.save()
        self._rebuild_merged()
        logger.debug(f"更新 meta_schema: {[f.key for f in schema]}")

    def get_output_fields(self) -> list[MetaFieldDef]:
        """返回输出元数据字段（scope=output 且 crop 有效），按定义顺序"""
        return [f for f in self._meta_schema
                if f.scope == "output" and f.crop is not None]

    def get_custom_input_fields(self) -> list[MetaFieldDef]:
        """返回非预制的输入元数据字段（排除 label/group/notes）"""
        return [f for f in self._meta_schema
                if f.scope == "input" and f.key not in _PREDEFINED_INPUT_KEYS]

    # ─── 匹配度阈值 ───────────────────────────────────

    def get_match_threshold(self) -> float:
        """返回匹配度阈值：local 覆盖 → system → 默认值"""
        if self._local_threshold is not None:
            return self._local_threshold
        if self._system_threshold is not None:
            return self._system_threshold
        return DEFAULT_MATCH_THRESHOLD

    def set_match_threshold(self, value: float):
        """设置并持久化匹配度阈值（开发→system；用户→local 覆盖，
        回设为 system 有效值时清除覆盖）"""
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


    def get_meta_options(self, field: MetaFieldDef) -> list[str]:
        """返回某 meta 字段的去重值列表，按 type/sort_by 排序

        - type="number"：按数值排序，数字优先于非数字文本，空值不在列表中
        - type="text"：按字典序升序
        """
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


    def get_entry(self, filename: str) -> ReferenceEntry | None:
        """按文件路径查找条目"""
        for e in self._entries:
            if e.file == filename:
                return e
        return None

    def next_filename(self) -> str:
        """返回新的文件名（如 'A1B2C3D4.png'）

        使用 UUID 前 8 位（大写字母）作为文件名，防止重复。
        不含桶路径，桶由 _save_image 随机分配。
        """
        return f"{uuid.uuid4().hex[:8].upper()}.png"

    # ─── 内部方法 ─────────────────────────────────────────

    def _save_image(self, layer_dir: Path, filename: str, image_data: "np.ndarray"):
        """保存 numpy 图像到指定层的随机桶目录

        Args:
            layer_dir: 层目录（system_dir 或 local_dir）
            filename: 文件名（不含桶路径）
            image_data: numpy 图像数据
        """
        import cv2
        # 随机选择一个桶
        if self._buckets:
            bucket = random.choice(self._buckets)
            bucket_dir = layer_dir / bucket
        else:
            bucket_dir = layer_dir
        bucket_dir.mkdir(parents=True, exist_ok=True)
        file_path = bucket_dir / filename
        # cv2.imwrite 不支持中文路径，用 imencode + 文件写入
        success, buf = cv2.imencode('.png', image_data)
        if success:
            file_path.write_bytes(buf.tobytes())
            logger.debug(f"保存参考图: {file_path} ({image_data.shape[1]}x{image_data.shape[0]})")
        else:
            logger.error(f"图片编码失败: {file_path}")

    def _delete_image(self, layer_dir: Path, filename: str):
        """删除指定层的图片文件（遍历全部桶查找，不存在则静默跳过）

        Args:
            layer_dir: 层目录（system_dir 或 local_dir）
            filename: 文件名（不含桶路径）
        """
        # 遍历全部桶查找文件
        for bucket in self._buckets:
            file_path = layer_dir / bucket / filename
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"删除参考图文件: {file_path}")
                return
        # 桶列表为空时直接在层目录查找
        if not self._buckets:
            file_path = layer_dir / filename
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"删除参考图文件: {file_path}")
