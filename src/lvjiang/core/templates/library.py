"""模板图库：分组、索引（``templates.yaml``）与引用关系。

模板图是 ``find`` / ``scan by image`` 用的**定位图**，与参考图库（``recognize`` 的分类器）
完全分离。目录即分组：``config/{system,local}/templates/<分组>/…​.png``。

分组只有两类，判定靠布局清单而不是约定：

- **布局分组**：分组名等于某个**根布局 key**（``extends`` 为空的那个）——``android``、
  ``desktop``。成员由场景编辑器截取并绑定到 Region，主要经 ``scan [scene].[region] by image``
  消费；它们的录制尺寸与阈值以 Layout 的 ``Region.template`` 为权威，**不进索引**。
- **普通分组**：其余名字（现为 ``common``）。跨布局共享、不绑定 Region，按名消费
  （``find … by image "common/x"``）；录制尺寸只能存在索引里，这也是 ``templates.yaml``
  存在的唯一理由。

索引是聚合键值文件，走 :class:`ConfigResolver` 的 system←local 深合并：system 全量、local
只存 diff（``__deleted__`` 删除条目）。``role`` 区分可寻址模板（``template``）与算法标定
样本（``sample``，例如装备锁图标——运行时按颜色比例判定，不做模板匹配，因此不需要尺寸）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..config.resolver import ConfigResolver, get_resolver
from ..layout.config import load_layout_entries
from ..recognizers.template_locator import (
    TEMPLATES_REL_DIR,
    delete_template,
    normalize_template_name,
)

#: 索引文件（相对配置层根）：放在 templates/ 里，和分组目录、README 同级
INDEX_REL = "templates/templates.yaml"

ROLE_TEMPLATE = "template"
ROLE_SAMPLE = "sample"
ROLES = (ROLE_TEMPLATE, ROLE_SAMPLE)

#: 脚本里 ``by image "名字"`` 的字面量；扫描按名引用的唯一可靠来源
_BY_IMAGE = re.compile(r'by\s+image\s+"([^"]+)"')


@dataclass(frozen=True)
class TemplateBinding:
    """一处 Region 绑定：布局 / 场景 / 区域。"""

    layout: str
    scene: str
    region: str

    def dsl(self) -> str:
        """可直接粘进脚本的候选写法。"""
        return f"[{self.scene}].{self.region}"


@dataclass(frozen=True)
class TemplateInfo:
    """一张模板图及其元数据、引用情况。"""

    name: str
    group: str
    layer: str = ""                 # system / local / ""（文件缺失）
    path: Path | None = None
    width: int = 0
    height: int = 0
    record_w: int = 0               # 0 = 未登记录制尺寸
    record_h: int = 0
    note: str = ""
    role: str = ROLE_TEMPLATE
    bindings: tuple[TemplateBinding, ...] = ()
    scripts: tuple[str, ...] = ()

    @property
    def is_layout_group(self) -> bool:
        return self.group in root_layout_groups()

    @property
    def has_record(self) -> bool:
        return self.record_w > 0 and self.record_h > 0

    @property
    def is_sample(self) -> bool:
        return self.role == ROLE_SAMPLE


# ─── 分组 ────────────────────────────────────────────────

def root_layout_groups() -> set[str]:
    """根布局 key 集合（``extends`` 为空的那些）——它们才是布局分组。"""
    return {key for key, entry in load_layout_entries().items() if not entry.extends}


def group_of(name: str) -> str:
    """模板逻辑名的第一段即分组。"""
    return normalize_template_name(name).split("/", 1)[0]


def is_layout_group(name: str) -> bool:
    """该模板是否属于布局分组（名字第一段等于根布局 key）。"""
    return group_of(name) in root_layout_groups()


def managed_by_scene(name: str) -> bool:
    """布局分组的模板归**场景管理**维护：图库管理只展示，不改名、不覆盖、不删除。

    两份入口同时改同一张图会破坏"名字 = 布局/场景/Region、尺寸 = Layout 绑定"这条
    契约，所以这里把所有权定死，而不是靠界面自觉。
    """
    return is_layout_group(name)


def _require_library_owned(name: str) -> None:
    if managed_by_scene(name):
        raise ValueError(
            f"布局分组的模板由场景管理维护，图库管理只读: {name}")


# ─── 索引读写 ────────────────────────────────────────────

def load_index(resolver: ConfigResolver | None = None) -> dict[str, dict]:
    """读取合并后的索引条目 ``{名字: {record_w, record_h, note, role}}``。"""
    data = (resolver or get_resolver()).load_merged(INDEX_REL)
    entries = data.get("templates")
    if not isinstance(entries, dict):
        return {}
    return {str(k): dict(v) for k, v in entries.items() if isinstance(v, dict)}


def _save_entry(name: str, entry: dict | None,
                resolver: ConfigResolver | None = None) -> None:
    """整份读→改一个条目→整份写（save_merged 的入参必须是完整文档）。"""
    cfg = resolver or get_resolver()
    doc = cfg.load_merged(INDEX_REL)
    entries = doc.get("templates")
    if not isinstance(entries, dict):
        entries = {}
    if entry is None:
        entries.pop(name, None)
    else:
        entries[name] = entry
    doc.setdefault("version", 1)
    doc["templates"] = entries
    cfg.save_merged(INDEX_REL, doc)


def set_entry(name: str, *, record_w: int | None = None,
              record_h: int | None = None, note: str | None = None,
              role: str | None = None,
              resolver: ConfigResolver | None = None) -> dict:
    """写入/更新一条索引条目；只覆盖点名给的字段，其余原样保留。

    ``record_w``/``record_h`` 传 0 表示"未登记"，不写进文件（避免 0 被当成有效尺寸）。
    """
    base = normalize_template_name(name)
    current = dict(load_index(resolver).get(base) or {})
    if record_w is not None or record_h is not None:
        w = int(record_w or 0)
        h = int(record_h or 0)
        if w > 0 and h > 0:
            current["record_w"] = w
            current["record_h"] = h
        elif w or h:
            raise ValueError("record_w 与 record_h 必须同时给正数")
        else:
            current.pop("record_w", None)
            current.pop("record_h", None)
    if note is not None:
        if str(note).strip():
            current["note"] = str(note).strip()
        else:
            current.pop("note", None)
    if role is not None:
        value = str(role)
        if value not in ROLES:
            raise ValueError(f"未知的模板角色: {role!r}")
        if value == ROLE_TEMPLATE:
            current.pop("role", None)     # 默认值不落盘
        else:
            current["role"] = value
    _save_entry(base, current or None, resolver)
    return current


def drop_entry(name: str, resolver: ConfigResolver | None = None) -> None:
    """删除一条索引条目（图片是否删除由调用方决定）。"""
    _save_entry(normalize_template_name(name), None, resolver)


# ─── 引用关系 ────────────────────────────────────────────

def bindings_by_name() -> dict[str, tuple[TemplateBinding, ...]]:
    """扫全部布局，收集 ``Region.template.name`` → 绑定位置。"""
    found: dict[str, list[TemplateBinding]] = {}
    for layout_key in load_layout_entries():
        layout = _load_layout(layout_key)
        if layout is None:
            continue
        for scene_key, regions in layout.regions.items():
            for region in regions:
                binding = getattr(region, "template", None)
                if binding is None:
                    continue
                found.setdefault(binding.name, []).append(
                    TemplateBinding(layout_key, scene_key, region.key))
    return {name: tuple(items) for name, items in found.items()}


def _load_layout(layout_key: str):
    from ..layout.storage import load_layout_by_key
    return load_layout_by_key(layout_key)


def script_refs() -> dict[str, tuple[str, ...]]:
    """扫工作流脚本里 ``by image "名字"`` 的字面量 → 名字 → 出现它的脚本。"""
    resolver = get_resolver()
    found: dict[str, list[str]] = {}
    for rel in resolver.enumerate_entity_tree("workflows", "*.wf"):
        path = resolver.resolve_read(f"workflows/{rel}")
        if path is None:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name in _BY_IMAGE.findall(text):
            found.setdefault(name, []).append(f"workflows/{rel}")
    return {name: tuple(sorted(set(items))) for name, items in found.items()}


# ─── 清单 ────────────────────────────────────────────────

def image_names(resolver: ConfigResolver | None = None) -> list[str]:
    """两层模板目录里的全部逻辑名（含分组前缀，去 ``.png``）。"""
    cfg = resolver or get_resolver()
    return [name[:-4] for name in cfg.enumerate_entity_tree(
        TEMPLATES_REL_DIR, "*.png")]


def _file_info(resolver: ConfigResolver, name: str) -> tuple[str, Path | None]:
    """该模板实际生效的文件与所在层（local 影子优先）。"""
    rel = f"{TEMPLATES_REL_DIR}/{name}.png"
    local = resolver.local_dir / rel
    if local.is_file():
        return "local", local
    system = resolver.system_dir / rel
    if system.is_file():
        return "system", system
    return "", None


def _image_size(path: Path | None) -> tuple[int, int]:
    if path is None:
        return 0, 0
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8),
                       cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim < 2:
        return 0, 0
    return int(img.shape[1]), int(img.shape[0])


def templates(*, resolver: ConfigResolver | None = None,
              include_samples: bool = True) -> list[TemplateInfo]:
    """列出全部模板（按名字排序），合并索引元数据与引用关系。"""
    cfg = resolver or get_resolver()
    index = load_index(cfg)
    bindings = bindings_by_name()
    scripts = script_refs()
    result: list[TemplateInfo] = []
    for name in image_names(cfg):
        entry = index.get(name) or {}
        layer, path = _file_info(cfg, name)
        width, height = _image_size(path)
        info = TemplateInfo(
            name=name,
            group=group_of(name),
            layer=layer,
            path=path,
            width=width,
            height=height,
            record_w=int(entry.get("record_w") or 0),
            record_h=int(entry.get("record_h") or 0),
            note=str(entry.get("note") or ""),
            role=str(entry.get("role") or ROLE_TEMPLATE),
            bindings=bindings.get(name, ()),
            scripts=scripts.get(name, ()),
        )
        if not include_samples and info.is_sample:
            continue
        result.append(info)
    return result


def template_info(name: str, *, resolver: ConfigResolver | None = None) -> TemplateInfo | None:
    base = normalize_template_name(name)
    return next((item for item in templates(resolver=resolver) if item.name == base), None)


# ─── 改名 / 删除 ─────────────────────────────────────────

def rename_template(old: str, new: str, *,
                    resolver: ConfigResolver | None = None) -> Path:
    """改名：先写新文件、再删旧文件，索引条目跟着搬。

    只对**非布局分组**开放（布局分组归场景管理）；目标分组也必须是普通分组，
    所以普通分组之间可以顺便移动。系统模板在用户模式下改名会被
    ``ensure_entity_deletable`` 拒绝——删不掉旧文件，改名没有意义。
    """
    cfg = resolver or get_resolver()
    old_name = normalize_template_name(old)
    new_name = normalize_template_name(new)
    _require_library_owned(old_name)
    _require_library_owned(new_name)
    layer, path = _file_info(cfg, old_name)
    if path is None:
        raise FileNotFoundError(f"模板不存在: {old_name}.png")
    old_rel = f"{TEMPLATES_REL_DIR}/{old_name}.png"
    cfg.ensure_entity_deletable(old_rel)
    if _file_info(cfg, new_name)[1] is not None:
        raise FileExistsError(f"目标名字已存在: {new_name}.png")

    # 原字节搬过去：改名不该顺带改变图片内容或压缩参数
    cfg.write_entity(f"{TEMPLATES_REL_DIR}/{new_name}.png",
                     Path(path).read_bytes(), force=True)
    entry = load_index(cfg).get(old_name)
    if entry:
        set_entry(new_name, resolver=cfg, **_entry_kwargs(entry))
        drop_entry(old_name, cfg)
    delete_template(old_name, cfg)
    logger.info(f"模板已改名: {old_name} -> {new_name}（层={layer}）")
    return cfg.resolve_read(f"{TEMPLATES_REL_DIR}/{new_name}.png") or Path(path)


def _entry_kwargs(entry: dict) -> dict:
    return {
        "record_w": int(entry.get("record_w") or 0),
        "record_h": int(entry.get("record_h") or 0),
        "note": str(entry.get("note") or ""),
        "role": str(entry.get("role") or ROLE_TEMPLATE),
    }


def remove_template(name: str, *,
                    resolver: ConfigResolver | None = None) -> bool:
    """删除模板图片（按层语义）并清掉索引条目；布局分组拒绝。"""
    cfg = resolver or get_resolver()
    base = normalize_template_name(name)
    _require_library_owned(base)
    removed = delete_template(base, cfg)
    if not removed:
        return False
    drop_entry(base, cfg)
    return True


__all__ = [
    "INDEX_REL",
    "ROLES",
    "ROLE_SAMPLE",
    "ROLE_TEMPLATE",
    "TemplateBinding",
    "TemplateInfo",
    "bindings_by_name",
    "drop_entry",
    "group_of",
    "image_names",
    "is_layout_group",
    "managed_by_scene",
    "load_index",
    "remove_template",
    "rename_template",
    "root_layout_groups",
    "script_refs",
    "set_entry",
    "template_info",
    "templates",
]
