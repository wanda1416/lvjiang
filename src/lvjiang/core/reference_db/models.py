"""参考图库的数据结构与常量。

图库空间（space）是图库内部完全独立的配置集，各自拥有 meta_schema、引用条目
与参考图；``DEFAULT_SPACE`` 是默认空间名，也是配置文件名，属于稳定数据 key，
不参与界面翻译。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from ...i18n import tr


class _SeedMetaField(TypedDict):
    """种子 meta 字段的字面量结构。

    用 TypedDict 而不是 dict[str, Any]：这份常量要经 ``MetaFieldDef(**d)``
    展开构造，写成 Any 会让那次展开完全不受检——键名拼错、类型写反都要等
    运行期才炸。TypedDict 既保住了 dict 的用法（下面还要 .get 取默认值），
    又让展开重新受 mypy 检查。
    """

    key: str
    name: str
    filterable: bool
    type: str
    sort_by: str

_SEED_META_SCHEMA: list[_SeedMetaField] = [
    {"key": "level", "name": tr("等级"), "filterable": True, "type": "number", "sort_by": "asc"},
]

_PREDEFINED_INPUT_KEYS = frozenset({"label", "group", "notes"})

REFERENCE_OUTPUT_RESERVED_KEYS = frozenset({"label", "group", "confidence", "meta"})

DEFAULT_SPACE = "手游"

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
