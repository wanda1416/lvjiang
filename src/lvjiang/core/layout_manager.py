"""布局配置管理器 - 布局 CRUD、截图管理、窗口标题配置

存储结构（目录化）：
- layouts.yaml          名册 + canvas 内联（聚合键值，local diff 合并）
- layouts/{key}/{scene_key}.json   每场景独立文件（实体影子，整文件覆盖）

布局别名（extends）：条目带 `extends: 根布局名` 时，scene 全部复用根布局
目录，仅 canvas 独立；别名自身不产生任何 scene 文件。
严格约束：extends 只能指向根布局（禁止多级继承）。
"""

import json
from functools import wraps

from loguru import logger

from ..i18n import tr
from .config.interface import get_interface_store
from .config.resolver import get_resolver
from .key_validation import validate_layout_activation_keys
from .layout_config import (
    DEFAULT_ASPECT_TOLERANCE,
    LayoutEntry,
    load_layout_doc,
    load_layout_entries,
    parse_layout_entries,
    save_layout_doc,
    validate_layout_key,
)
from .layout_models import CanvasConfig, Layout
from .layout_references import (
    _delete_layout_item_key as _delete_layout_item_key,
)
from .layout_references import (
    _rename_layout_item_key as _rename_layout_item_key,
)
from .layout_references import (
    delete_item_key_across_all_layouts as delete_item_key_across_all_layouts,
)
from .layout_references import (
    delete_scene_across_all_layouts as delete_scene_across_all_layouts,
)
from .layout_references import (
    migrate_layout_item as migrate_layout_item,
)
from .layout_references import (
    rename_item_key_across_all_layouts as rename_item_key_across_all_layouts,
)
from .layout_references import (
    rename_layout_scene_key as rename_layout_scene_key,
)
from .layout_references import (
    rename_scene_across_all_layouts as rename_scene_across_all_layouts,
)
from .layout_screenshots import (
    _existing_screenshot_path as _existing_screenshot_path,
)
from .layout_screenshots import (
    _load_screenshot_meta as _load_screenshot_meta,
)
from .layout_screenshots import (
    _safe_name as _safe_name,
)
from .layout_screenshots import (
    _save_screenshot_meta as _save_screenshot_meta,
)
from .layout_screenshots import (
    _screenshot_base_stem as _screenshot_base_stem,
)
from .layout_screenshots import (
    _screenshot_meta_path as _screenshot_meta_path,
)
from .layout_screenshots import (
    copy_screenshots as copy_screenshots,
)
from .layout_screenshots import (
    delete_scene_screenshot as delete_scene_screenshot,
)
from .layout_screenshots import (
    delete_screenshots as delete_screenshots,
)
from .layout_screenshots import (
    get_active_screenshot_index as get_active_screenshot_index,
)
from .layout_screenshots import (
    get_scene_screenshot_format as get_scene_screenshot_format,
)
from .layout_screenshots import (
    layout_screenshots_dir as layout_screenshots_dir,
)
from .layout_screenshots import (
    list_scene_screenshots as list_scene_screenshots,
)
from .layout_screenshots import (
    load_scene_screenshot as load_scene_screenshot,
)
from .layout_screenshots import (
    reindex_scene_screenshots as reindex_scene_screenshots,
)
from .layout_screenshots import (
    rename_scene_screenshots as rename_scene_screenshots,
)
from .layout_screenshots import (
    rename_view_screenshots as rename_view_screenshots,
)
from .layout_screenshots import (
    save_scene_screenshot as save_scene_screenshot,
)
from .layout_screenshots import (
    scene_screenshot_name as scene_screenshot_name,
)
from .layout_screenshots import (
    set_active_screenshot_index as set_active_screenshot_index,
)
from .layout_screenshots import (
    set_scene_screenshot_format as set_scene_screenshot_format,
)
from .layout_storage import _LAYOUTS_YAML_REL
from .layout_storage import (
    _drop_orphan_coords as _drop_orphan_coords,
)
from .layout_storage import (
    _enumerate_scene_files as _enumerate_scene_files,
)
from .layout_storage import (
    _expand_scene_references as _expand_scene_references,
)
from .layout_storage import (
    _layout_dir_rel as _layout_dir_rel,
)
from .layout_storage import (
    _resolve_layout_entry as _resolve_layout_entry,
)
from .layout_storage import (
    _scene_rel as _scene_rel,
)
from .layout_storage import (
    expand_one_reference as expand_one_reference,
)
from .layout_storage import (
    load_layout_by_key as load_layout_by_key,
)
from .layout_storage import (
    refresh_scene_references as refresh_scene_references,
)
from .layout_storage import (
    scene_layout_rel as scene_layout_rel,
)
from .layout_storage import (
    scene_layout_rels as scene_layout_rels,
)
from .layout_storage import (
    shared_layout_bindings as shared_layout_bindings,
)
from .scene_registry import (
    get_panel_defs,
    get_point_defs,
    get_region_defs,
)


def _checked_layout_save(function):
    @wraps(function)
    def wrapped(self, layout, changed_scenes=None, **kwargs):
        from .config.conflicts import ConfigWriteConflict, checked_write, file_baseline
        resolver = get_resolver()
        self.last_save_error = ""
        scenes = (set(layout.regions) | set(layout.points) | set(layout.arrows)
                  | set(layout.panels) | set(layout.subscene_refs))
        if changed_scenes is not None:
            scenes &= changed_scenes
        names = scene_layout_rels(layout.key, scenes).values()
        names = set(names) | {"layouts.yaml"}
        baselines = {name: layout._file_baselines.get(name, file_baseline(resolver, name))
                     for name in names}
        def save_batch():
            with resolver.batch_changes():
                result = function(self, layout, changed_scenes, **kwargs)
                if result:
                    # 在写锁内捕获本次写入基线；未写场景仍保留打开时的基线。
                    layout._file_baselines.update({name: file_baseline(resolver, name) for name in names})
                return result
        try:
            result = checked_write(resolver, baselines, save_batch)
        except ConfigWriteConflict as exc:
            self.last_save_error = str(exc)
            logger.info("布局未保存：{}", exc)
            return False
        return result
    return wrapped


class LayoutConfigManager:
    """管理布局配置的持久化

    布局存储为目录结构：layouts.yaml（key/name 名册+canvas）+
    layouts/{key}/{scene}.json；
    读写经 ConfigResolver（开发→system，用户→local 影子）；
    session.json 只在 actives.layout 记录激活布局。
    """

    def __init__(self):
        self._config = self._load_config()
        self.last_save_error = ""

    def _load_config(self) -> dict:
        """从 interface.json 读 actives.layout。

        layout_manager 只管理激活布局，不触碰 interface.json 其他节点。
        """
        return {"active_layout": get_interface_store().get_active("layout", "")}

    def _save_config(self):
        """保存 actives.layout。"""
        get_interface_store().set_active(
            "layout", self._config.get("active_layout", ""))

    def _reload_config(self):
        """从文件重新加载配置（多实例同步）"""
        self._config = self._load_config()

    # ─── 布局 CRUD ──────────────────────────────────────

    def list_layout_keys(self) -> list[str]:
        """返回稳定布局 key，顺序与 ``layouts.yaml`` 一致。"""
        return list(load_layout_entries())

    def list_layout_entries(self) -> list[LayoutEntry]:
        return list(load_layout_entries().values())

    def get_layout_name(self, key: str) -> str:
        entry = load_layout_entries().get(key)
        return entry.name if entry is not None else key

    def new_layout(
        self, key: str, name: str, *, activate: bool = True,
    ) -> Layout:
        """创建空布局（所有场景初始为空 regions）

        Raises:
            ValueError: 布局名已存在（含别名）时。新建撞名别名会把空场景
                全量写入根布局目录，造成根布局数据被清空，必须拒绝。
        """
        key = validate_layout_key(key)
        name = name.strip()
        self.validate_new_identity(key, name)
        from .scene_registry import SCENE_REGIONS
        layout = Layout(key=key, name=name)
        for scene_key in SCENE_REGIONS:
            layout.regions[scene_key] = []
        if not self.save_layout(layout):
            raise ValueError(self.last_save_error or "布局未保存，请检查日志")
        if activate:
            self.set_active_layout(key)
        logger.info(f"布局已新建: {name} ({key})")
        return layout

    def validate_new_identity(self, key: str, name: str) -> None:
        key = validate_layout_key(key)
        name = name.strip()
        entries = load_layout_entries()
        if key in entries:
            raise ValueError(f"布局 key 已存在: {key}")
        if not name:
            raise ValueError("布局名称不能为空")
        if any(entry.name == name for entry in entries.values()):
            raise ValueError(f"布局名称已存在: {name}")

    def is_alias_layout(self, name: str) -> bool:
        """判断布局是否为别名（yaml 条目带 extends）"""
        resolver = get_resolver()
        merged = load_layout_doc(resolver)
        entry = merged.get("layouts", {}).get(name) or {}
        return bool(entry.get("extends"))

    def create_alias_layout(
        self, key: str, name: str, extends_name: str, canvas: CanvasConfig,
    ) -> Layout | None:
        """创建别名布局：仅 yaml 条目（extends + canvas），无 scene 文件

        Args:
            name: 新布局名称
            extends_name: 继承的根布局名称
            canvas: 画布配置

        Returns:
            创建的 Layout 对象，失败时返回 None
        """
        try:
            key = validate_layout_key(key)
            name = name.strip()
            self.validate_new_identity(key, name)
        except ValueError as exc:
            logger.error(str(exc))
            return None
        if extends_name not in self.list_layout_keys():
            logger.error(f"继承目标不存在: {extends_name}")
            return None
        if self.is_alias_layout(extends_name):
            logger.error(f"继承目标必须是根布局，不能是别名: {extends_name}")
            return None

        # 写入 yaml 条目
        resolver = get_resolver()
        merged = load_layout_doc(resolver)
        layouts_doc = merged.setdefault("layouts", {})
        layouts_doc[key] = {
            "name": name,
            "extends": extends_name,
            "canvas": canvas.to_dict(),
        }
        save_layout_doc(merged, resolver)

        # 加载并返回（scene 从根布局读取）
        layout = self.load_layout(key)
        if layout:
            logger.info(
                f"别名布局已创建: {name} ({key}, extends {extends_name})")
        return layout

    def load_layout(self, name: str) -> "Layout | None":
        layout = load_layout_by_key(name)
        if layout is None:
            logger.warning(f"布局不存在: {name}")
        else:
            logger.info(f"布局已加载: {name}")
            from .config.conflicts import file_baseline
            from .scene_registry import get_registry
            paths = set(scene_layout_rels(name, get_registry().all_scene_keys()).values()) | {"layouts.yaml"}
            layout._file_baselines = {path: file_baseline(get_resolver(), path) for path in paths}
        return layout

    @_checked_layout_save
    def save_layout(self, layout: Layout, changed_scenes: set[str] | None = None) -> bool:
        """保存布局

        Args:
            layout: 布局对象
            changed_scenes: 需要写盘的场景 key 集合。
                None = 全量写盘（新建布局、另存为等场景）；
                set = 增量写盘，只写指定场景的 JSON 文件。

        别名布局（yaml 条目带 extends）：保留 extends 字段，
        scene 文件写入根布局目录（别名自身不维护 scene）。
        extends 条目非法（目标缺失/多级继承）时拒绝写盘。
        """
        # 持久化入口做最终门禁：UI 表单会实时校验，但导入、
        # 测试替身或其他调用方仍可能构造出非法的内存布局。
        try:
            validate_layout_activation_keys(layout)
        except ValueError as exc:
            logger.error(f"布局 [{layout.name}] 按键校验失败，拒绝保存：{exc}")
            return False

        resolver = get_resolver()

        # 1. 校验 extends 合法性（与加载侧对称：目标缺失/多级继承拒绝写盘）
        merged = load_layout_doc(resolver)
        layouts_doc = merged.setdefault("layouts", {})
        existing = layouts_doc.get(layout.key) or {}
        if not existing:
            try:
                validate_layout_key(layout.key)
                self.validate_new_identity(layout.key, layout.name)
            except ValueError as exc:
                logger.error(str(exc))
                return False
            scene_dir_name = layout.key
        else:
            resolved = _resolve_layout_entry(layouts_doc, layout.key)
            if resolved is None:
                logger.error(f"布局 [{layout.key}] 的 extends 条目非法，拒绝保存")
                return False
            _, scene_dir_name, _, _ = resolved

        # 2. 更新 layouts.yaml 中的条目（别名布局保留 extends）
        entry_out: dict = {}
        entry_out["name"] = layout.name
        if existing.get("extends"):
            entry_out["extends"] = existing["extends"]
        # 保留 desc 字段（如果有）
        if layout.desc:
            entry_out["desc"] = layout.desc
        elif existing.get("desc"):
            entry_out["desc"] = existing["desc"]
        entry_out["canvas"] = layout.canvas.to_dict()
        # 与 desc 同理：内存布局没带就沿用清单里的旧值。清空要走布局信息编辑，
        # 那条路径直接改清单——保存场景坐标顺手抹掉画布尺寸要求太容易了
        if layout.aspect:
            entry_out["aspect"] = layout.aspect
        elif existing.get("aspect"):
            entry_out["aspect"] = existing["aspect"]
        if existing.get("aspect_tolerance") is not None:
            entry_out["aspect_tolerance"] = existing["aspect_tolerance"]
        elif layout.aspect_tolerance != DEFAULT_ASPECT_TOLERANCE:
            entry_out["aspect_tolerance"] = layout.aspect_tolerance
        layouts_doc[layout.key] = entry_out
        try:
            save_layout_doc(merged, resolver)
        except ValueError as exc:
            logger.error(f"布局清单无效，拒绝保存：{exc}")
            return False

        # 3. 写场景 JSON 文件（增量或全量）；别名布局落到根布局目录
        all_scene_keys = (set(layout.regions) | set(layout.points) | set(layout.arrows)
                          | set(layout.panels) | set(layout.crop_canvases)
                          | set(layout.subscene_refs))
        scene_keys = all_scene_keys if changed_scenes is None else (
            changed_scenes & all_scene_keys)
        for sk in scene_keys:
            entry: dict = {}
            # 按场景定义顺序排序 regions/points/panels，避免编辑顺序影响输出
            region_order = {r.key: i for i, r in enumerate(get_region_defs(sk))}
            # 引用项属于源场景，写回就烘死成拷贝，源场景改坐标不再同步。
            # Region.to_dict 也剔了 source_scene，这里是第二道闸。
            regions = [r for r in (layout.regions.get(sk) or [])
                       if not getattr(r, "source_scene", "")]
            entry["regions"] = [r.to_dict() for r in sorted(regions, key=lambda r: region_order.get(r.key, 999))]
            pts = [p for p in (layout.points.get(sk) or [])
                   if not getattr(p, "source_scene", "")]
            if pts:
                point_order = {p.key: i for i, p in enumerate(get_point_defs(sk))}
                entry["points"] = [p.to_dict() for p in sorted(pts, key=lambda p: point_order.get(p.key, 999))]
            arrs = layout.arrows.get(sk) or []
            if arrs:
                entry["arrows"] = [a.to_dict() for a in arrs]  # arrows 无定义顺序，保持原样
            pnls = layout.panels.get(sk) or []
            if pnls:
                panel_order = {p.key: i for i, p in enumerate(get_panel_defs(sk))}
                entry["panels"] = [p.to_dict() for p in sorted(pnls, key=lambda p: panel_order.get(p.key, 999))]
            if sk in layout.crop_canvases:
                entry["crop_canvas"] = layout.crop_canvases[sk].to_dict()
            refs = layout.subscene_refs.get(sk) or []
            if refs:
                entry["subscene_refs"] = [r.to_dict() for r in refs]
            positions = []
            for item in (*layout.regions.get(sk, []), *layout.points.get(sk, [])):
                if not (item.source_scene and item.position_overridden):
                    continue
                if hasattr(item, "x_ratio"):
                    x_ratio, y_ratio = item.x_ratio, item.y_ratio
                else:
                    x_ratio, y_ratio = item.cx_ratio, item.cy_ratio
                positions.append({
                    "scene": item.source_scene,
                    "entity": item.key,
                    "x_ratio": x_ratio,
                    "y_ratio": y_ratio,
                })
            if positions:
                entry["reference_positions"] = positions
            rel_path = _scene_rel(scene_dir_name, sk)
            resolver.write_entity(
                rel_path,
                json.dumps(entry, ensure_ascii=False, indent=2),
                layer=resolver.local_only_layer(rel_path),
            )
        mode = f"增量 {len(scene_keys)}/{len(all_scene_keys)} 场景" if changed_scenes is not None else tr("全量")
        logger.info(f"布局已保存: {layout.name} ({layout.key}, {mode})")
        return True

    @staticmethod
    def is_system_layout(name: str) -> bool:
        """布局是否由 system 层声明。

        别名布局没有自己的场景目录，因此不能通过场景文件是否存在来判断来源。
        ``layouts.yaml`` 的 system 名册才是布局身份的单一事实源。
        """
        resolver = get_resolver()
        system_doc = resolver.load_system(_LAYOUTS_YAML_REL)
        if not system_doc:
            return False
        return name in parse_layout_entries(system_doc)

    def delete_layout(self, name: str) -> bool:
        resolver = get_resolver()
        # 确认布局存在
        merged = load_layout_doc(resolver)
        layouts_doc = merged.get("layouts", {})
        scene_keys = _enumerate_scene_files(name)
        if name not in layouts_doc and not scene_keys:
            return False

        # 拒绝删除被别名引用的根布局（避免悬空 extends）
        referencing = [n for n, e in layouts_doc.items()
                       if isinstance(e, dict) and e.get("extends") == name]
        if referencing:
            logger.error(f"布局 [{name}] 被别名布局引用: {referencing}，拒绝删除")
            return False
        # 系统布局属于 system 内容，用户模式下不可删除——不想用就别选它
        if not resolver.is_dev_mode() and self.is_system_layout(name):
            logger.error(f"布局 [{name}] 由系统配置提供，用户模式下不可删除")
            return False

        # 删除场景文件
        for sk in scene_keys:
            resolver.delete_entity(_scene_rel(name, sk))

        # 从 layouts.yaml 移除条目
        if name in layouts_doc:
            del layouts_doc[name]
            save_layout_doc(merged, resolver)

        # 开发模式额外清理空目录
        if resolver.is_dev_mode():
            import shutil as _shutil
            dir_path = resolver.system_dir / _layout_dir_rel(name)
            if dir_path.is_dir() and not any(dir_path.iterdir()):
                _shutil.rmtree(dir_path)

        if self._config.get("active_layout") == name:
            self._config["active_layout"] = ""
            self._save_config()
        logger.info(f"布局已删除: {name}")
        return True

    def migrate_item_across_layouts(self, source: str, target: str, kind: str, key: str) -> list[str]:
        """在所有布局文件中把指定 key 的 region/point/panel 从 source 场景迁到 target 场景

        Returns:
            实际发生改动并已写盘的布局名称列表
        """
        changed = []
        for name in self.list_layout_keys():
            layout = self.load_layout(name)
            if layout is None:
                continue
            if migrate_layout_item(layout, source, target, kind, key):
                self.save_layout(layout)
                changed.append(name)
        if changed:
            logger.info(f"跨场景迁移 {kind}「{key}」: {source} -> {target}, 更新布局: {changed}")
        return changed


    # ─── 激活布局 ────────────────────────────────────────

    def get_active_layout_key(self) -> str:
        self._reload_config()
        name = self._config.get("active_layout", "")
        layouts = self.list_layout_keys()
        if isinstance(name, str) and name in layouts:
            return name
        # 未指定或 session 中仍引用已删除/改名的布局时，回退到首个有效
        # key。schema v2 放弃旧布局数据兼容，但失效的会话选中态不能让
        # 场景编辑器看似选中了首项、实际却没有加载任何布局。
        if layouts:
            fallback = layouts[0]
            if name:
                logger.warning(
                    f"active_layout 不存在: {name}，自动使用: {fallback}")
            else:
                logger.info(f"未指定 active_layout，自动使用: {fallback}")
            return fallback
        return ""

    def set_active_layout(self, name: str):
        self._config["active_layout"] = name
        self._save_config()
