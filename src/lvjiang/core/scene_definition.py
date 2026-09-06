"""场景定义加载器 - 从 YAML 文件加载场景配置

定义时数据类（ViewDef / RegionDef / PointDef / PanelDef / SceneDef）
已拆分至 scene_definition_models.py。
"""

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ..i18n import tr
from .config.resolver import get_resolver
from .scene_config import build_scene_doc, save_scene_doc
from .scene_definition_models import (
    BASE_VIEW_KEY,
    BASE_VIEW_NAME,
    VALID_REGION_TYPES,
    PanelDef,
    PointDef,
    RegionDef,
    SceneDef,
    SceneRefDef,
    SubsceneRefDef,
    ViewDef,
)

_VALID_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_key_and_name(key: str, name: str, kind: str) -> None:
    if not _VALID_KEY_RE.fullmatch(key):
        raise ValueError(f"{kind} key 必须以小写字母开头，仅含小写字母/数字/下划线")
    if not name.strip():
        raise ValueError(f"{kind}名称不能为空")


def _view_fields(item) -> dict:
    """序列化归属视图：单归属仍写 ``view:`` 保持既有文件不产生无谓 diff，
    多归属才写 ``views:``。读取侧两种都认。"""
    views = [v for v in (getattr(item, "views", None) or []) if v]
    if not views:
        return {}
    if len(views) == 1:
        return {"view": views[0]}
    return {"views": views}



class SceneRegistry:
    """
    场景注册表：从 YAML 目录加载全部场景定义。

    每个 .yaml 文件定义一个场景，格式：
        key: scene_key
        name: 场景名称
        regions:
          - key: region_key
            name: 区域名称
            type: attr|slot|func
            is_text: true|false
            is_clickable: true|false

    分组结构：
        group_config: {group_key: [scene_key, ...]}
        group_names:  {group_key: group_name}
    """

    def __init__(
        self,
        resolver=None,
        scene_order: list[str] | None = None,
        group_config: dict[str, list[str]] | None = None,
        group_names: dict[str, str] | None = None,
        disabled_scenes: set[str] | None = None,
    ):
        self._resolver = resolver or get_resolver()
        self._scenes: dict[str, SceneDef] = {}
        self._order: list[str] = []
        # 分组数据
        self._groups: dict[str, str] = {}            # group_key -> group_name
        self._group_order: list[str] = []            # 分组顺序
        self._group_scenes: dict[str, list[str]] = {}  # group_key -> [scene_key, ...]
        self._disabled_scenes = set(disabled_scenes or ())
        self._load_all(scene_order, group_config, group_names)

    def _load_all(
        self,
        scene_order: list[str] | None,
        group_config: dict[str, list[str]] | None = None,
        group_names: dict[str, str] | None = None,
    ):
        """加载场景：按 scene_order 指定的顺序，未指定的按文件名追加"""
        for name in self._resolver.enumerate_entities("scenes", "*.yaml"):
            yaml_file = self._resolver.resolve_read(f"scenes/{name}")
            if yaml_file is None:
                continue
            try:
                scene = self._load_scene(yaml_file)
                self._scenes[scene.key] = scene
                logger.debug(f"已加载场景: {scene.key}（{len(scene.regions)} 个区域）")
            except Exception as e:
                logger.error(f"加载场景配置失败 {yaml_file.name}: {e}")

        self._drop_invalid_references()

        # 按 scene_order 排序
        if scene_order:
            self._order = [k for k in scene_order if k in self._scenes]
            # 追加未在 order 中列出的场景
            for k in self._scenes:
                if k not in self._order:
                    self._order.append(k)
        else:
            self._order = list(self._scenes.keys())

        # 初始化分组
        self._init_groups(group_config, group_names)

    def _drop_invalid_references(self) -> None:
        """跨场景引用的合法性校验；只能在全部场景加载完之后做。

        非法引用**丢弃并记 error**，不抛异常：单个场景写错不该让整个注册表
        加载失败——那会连带 UI 起不来。丢弃后该 key 就是未定义，运行到它时
        走现有的"实体不存在"报错路径，同样不会静默点错地方。
        """
        for scene in self._scenes.values():
            kept: list[SceneRefDef] = []
            for ref in scene.references:
                source = self._scenes.get(ref.scene)
                where = f"场景 {scene.key} 引用 {ref.scene}.{ref.entity}"
                if source is None:
                    logger.error(f"{where} 失败：源场景不存在")
                    continue
                if source.is_subscene:
                    # 子场景实体坐标相对外框，搬过来需要变换；一级场景才是
                    # 同一套画布归一化坐标，可以零变换直取。
                    logger.error(f"{where} 失败：只能引用一级场景，不能引用子场景")
                    continue
                if any(r.entity == ref.entity for r in source.references):
                    logger.error(f"{where} 失败：不能引用引用，源必须是原生定义")
                    continue
                native = ([r.key for r in source.regions]
                          + [p.key for p in source.points]
                          + [p.key for p in source.panels])
                if ref.entity not in native:
                    logger.error(f"{where} 失败：源场景没有该实体")
                    continue
                kept.append(ref)
            scene.references = kept

    def _load_scene(self, yaml_file: Path) -> SceneDef:
        """从单个 YAML 文件加载场景定义"""
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(tr("YAML 顶层必须是字典"))

        key = data.get("key")
        name = data.get("name")
        if not key or not name:
            raise ValueError(tr("场景必须包含 key 和 name"))

        views = []
        for vd in data.get("views", []):
            views.append(ViewDef(
                key=vd["key"], name=vd.get("name", vd["key"]),
                same_layer=bool(vd.get("same_layer", True))))
        view_keys = {v.key for v in views}
        scene_type = str(data.get("type", "scene"))
        if scene_type not in ("scene", "subscene"):
            raise ValueError(f"场景 {key} 的 type 必须为 scene 或 subscene")
        if scene_type == "subscene" and views:
            raise ValueError(f"子场景 {key} 不能启用多视图")

        def _views_of(d: dict) -> list[str]:
            """读取并校验归属视图列表。

            兼容旧的单值 ``view:``；新格式用 ``views: [a, b]``。同一个按钮
            出现在多个视图里是常态（``close_btn`` 在结果视图和返还视图都在），
            坐标只有一份、跟布局走，所以多归属不影响坐标。
            """
            raw = d.get("views")
            if raw is None:
                single = d.get("view", "") or ""
                raw = [single] if single else []
            elif isinstance(raw, str):
                raw = [raw]
            elif not isinstance(raw, list):
                raise ValueError(f"定义 {d.get('key')} 的 views 必须是列表")
            result: list[str] = []
            for v in (str(x) for x in raw):
                if not views:
                    # 未开启多视图：一律视为基底，不保留归属
                    continue
                if v in ("", BASE_VIEW_KEY):
                    v = BASE_VIEW_KEY
                elif v not in view_keys:
                    raise ValueError(
                        f"定义 {d.get('key')} 的 view '{v}' 未在场景 views 中声明"
                    )
                if v not in result:
                    result.append(v)
            return result

        regions = []
        for rd in data.get("regions", []):
            rtype = rd.get("type", "info")
            if rtype not in VALID_REGION_TYPES:
                raise ValueError(f"区域 {rd.get('key')} 的 type '{rtype}' 不合法，合法值: {VALID_REGION_TYPES}")
            regions.append(RegionDef(
                key=rd["key"],
                name=rd["name"],
                type=rtype,
                is_text=rd.get("is_text", True),
                is_clickable=rd.get("is_clickable", False),
                views=_views_of(rd),
                to=str(rd.get("to", "") or ""),
            ))

        points = []
        for pd in data.get("points", []):
            ptype = pd.get("type", "func")
            if ptype not in VALID_REGION_TYPES:
                raise ValueError(f"坐标点 {pd.get('key')} 的 type '{ptype}' 不合法，合法值: {VALID_REGION_TYPES}")
            points.append(PointDef(
                key=pd["key"],
                name=pd["name"],
                type=ptype,
                is_text=pd.get("is_text", False),
                is_clickable=pd.get("is_clickable", True),
                views=_views_of(pd),
                to=str(pd.get("to", "") or ""),
            ))

        panels = []
        for pd in data.get("panels", []):
            panels.append(PanelDef(
                key=pd["key"],
                name=pd.get("name", pd["key"]),
                min_visible=float(pd.get("min_visible", 0.95)),
                views=_views_of(pd),
                calibration=str(pd.get("calibration", "auto")),
                scroll_direction=str(pd.get("scroll_direction", "vertical")),
            ))

        subscene_refs = []
        for rd in data.get("subscene_refs", []):
            subscene_refs.append(SubsceneRefDef(
                key=rd["key"],
                name=rd.get("name", rd["key"]),
                scene=rd["scene"],
                views=_views_of(rd),
            ))
        if scene_type == "subscene" and subscene_refs:
            raise ValueError(f"子场景 {key} 不能嵌套引用其他子场景")

        references = []
        for rd in data.get("references", []):
            source = rd.get("scene")
            entity = rd.get("entity")
            if not source or not entity:
                raise ValueError(f"场景 {key} 的 references 必须含 scene 和 entity")
            if source == key:
                raise ValueError(f"场景 {key} 不能引用自身")
            references.append(SceneRefDef(
                scene=str(source), entity=str(entity), views=_views_of(rd)))
        if scene_type == "subscene" and references:
            raise ValueError(f"子场景 {key} 不能引用其他场景的 area")

        # region / point / panel / 引用在同场景内共享命名空间，key 不得重复。
        # 引用与原生同名时**直接报错，绝不静默覆盖**——在 RPA 里"点错地方"
        # 的代价太高，宁可保存不了也不能让两个定义抢同一个 key。
        all_keys = ([r.key for r in regions] + [p.key for p in points]
                    + [p.key for p in panels] + [r.key for r in subscene_refs]
                    + [r.key for r in references])
        dup = {k for k in all_keys if all_keys.count(k) > 1}
        if dup:
            raise ValueError(f"场景 {key} 的 region/point/panel key 重复: {dup}")

        return SceneDef(key=key, name=name, regions=regions, points=points,
                        panels=panels, views=views, type=scene_type,
                        subscene_refs=subscene_refs, references=references)

    def get_scene(self, key: str) -> SceneDef | None:
        """获取场景定义，不存在返回 None"""
        return self._scenes.get(key)

    def all_scene_keys(self) -> list[str]:
        """返回所有已注册的场景 key 列表（按配置顺序）"""
        return list(self._order)

    def all_scenes(self) -> dict[str, SceneDef]:
        """返回所有场景的 {key: SceneDef} 字典（按配置顺序）"""
        return {k: self._scenes[k] for k in self._order}

    # ─── 分组管理 ──────────────────────────────────────────

    def _init_groups(
        self,
        group_config: dict[str, list[str]] | None,
        group_names: dict[str, str] | None,
    ):
        """初始化分组结构"""
        if not group_config:
            return
        self._group_order = list(group_config.keys())
        for gk in self._group_order:
            self._groups[gk] = (group_names or {}).get(gk, gk)
            self._group_scenes[gk] = [k for k in group_config[gk] if k in self._scenes]
        # 确保所有场景都在某个分组中
        assigned = set()
        for scenes in self._group_scenes.values():
            assigned.update(scenes)
        unassigned = [k for k in self._order if k not in assigned]
        if unassigned:
            first_group = self._group_order[0]
            self._group_scenes[first_group].extend(unassigned)

    def get_groups(self) -> list[tuple[str, str]]:
        """返回分组列表 [(key, name), ...]，按配置顺序"""
        return [(gk, self._groups[gk]) for gk in self._group_order]

    def get_group_name(self, group_key: str) -> str:
        """获取分组名称"""
        return self._groups.get(group_key, group_key)

    def get_group_scenes(self, group_key: str) -> list[str]:
        """获取分组下可见的场景 key；disabled 场景仍注册但不展示。"""
        return [key for key in self._group_scenes.get(group_key, [])
                if key not in self._disabled_scenes]

    def get_scene_group(self, scene_key: str) -> str | None:
        """获取场景所在的分组 key"""
        for gk, scenes in self._group_scenes.items():
            if scene_key in scenes:
                return gk
        return None

    def create_group(self, key: str, name: str):
        """创建新分组"""
        _validate_key_and_name(key, name, tr("分组"))
        if key in self._groups:
            raise ValueError(f"分组 key 已存在: {key}")
        self._groups[key] = name
        self._group_order.append(key)
        self._group_scenes[key] = []
        logger.info(f"已创建分组: {key} ({name})")

    def rename_group(self, key: str, new_name: str):
        """重命名分组（key 不可变）"""
        if key not in self._groups:
            raise ValueError(f"分组不存在: {key}")
        if not new_name.strip():
            raise ValueError(tr("分组名称不能为空"))
        self._groups[key] = new_name
        logger.info(f"已重命名分组: {key} -> {new_name}")

    def rename_group_key(self, old_key: str, new_key: str, new_name: str):
        """重命名分组 key（修改 scenes.yaml 中的 group_key）

        同时更新内存中的 _groups / _group_order / _group_scenes。
        调用方需随后调用 save_group_config() 持久化。
        """
        if old_key not in self._groups:
            raise ValueError(f"分组不存在: {old_key}")
        _validate_key_and_name(new_key, new_name, tr("分组"))
        if new_key != old_key and new_key in self._groups:
            raise ValueError(f"分组 key 已存在: {new_key}")
        # 更新 _group_scenes
        scenes = self._group_scenes.pop(old_key, [])
        self._group_scenes[new_key] = scenes
        # 更新 _groups
        self._groups.pop(old_key)
        self._groups[new_key] = new_name
        # 更新 _group_order
        idx = self._group_order.index(old_key)
        self._group_order[idx] = new_key
        logger.info(f"已重命名分组 key: {old_key} -> {new_key} ({new_name})")

    def delete_group(self, key: str):
        """删除空分组（非空抛异常）"""
        if key not in self._groups:
            raise ValueError(f"分组不存在: {key}")
        if len(self._groups) <= 1:
            raise ValueError(tr("至少需要保留一个场景分组"))
        scenes = self._group_scenes.get(key, [])
        if scenes:
            raise ValueError(f"分组非空，无法删除: {key}（包含 {len(scenes)} 个场景）")
        del self._groups[key]
        self._group_order.remove(key)
        del self._group_scenes[key]
        logger.info(f"已删除分组: {key}")

    def move_scene_to_group(self, scene_key: str, new_group_key: str):
        """移动场景到其他分组"""
        if scene_key not in self._scenes:
            raise ValueError(f"场景不存在: {scene_key}")
        if new_group_key not in self._groups:
            raise ValueError(f"目标分组不存在: {new_group_key}")
        # 从原分组移除
        old_group = self.get_scene_group(scene_key)
        if old_group:
            self._group_scenes[old_group].remove(scene_key)
        # 添加到新分组
        if scene_key not in self._group_scenes[new_group_key]:
            self._group_scenes[new_group_key].append(scene_key)
        logger.info(f"已移动场景 {scene_key} 到分组 {new_group_key}")

    def reorder_groups(self, new_order: list[str]):
        """更新分组顺序（仅内存）"""
        self._group_order = [k for k in new_order if k in self._groups]
        for k in self._groups:
            if k not in self._group_order:
                self._group_order.append(k)

    def save_group_config(self):
        """将分组配置以 schema v2 写入 scenes.yaml。"""
        data = build_scene_doc(
            self._group_order, self._group_scenes, self._groups,
            self._disabled_scenes)
        save_scene_doc(self._resolver, data)
        logger.info(f"已保存分组配置: {self._group_order}")

    # ─── 场景 CRUD ──────────────────────────────────────────

    def create_scene(self, key: str, name: str, group_key: str | None = None) -> SceneDef:
        """创建新场景 YAML 文件并注册，可选指定分组"""
        _validate_key_and_name(key, name, tr("场景"))
        if key in self._scenes:
            raise ValueError(f"场景 key 已存在: {key}")
        scene = SceneDef(key=key, name=name)
        self._save_scene_yaml(scene)

        # write_entity 会同步通知 scene_registry 热重载。新文件此时尚未写入
        # scenes.yaml，重载会把这个“未分组场景”临时归入第一个分组，并把
        # 当前 SceneRegistry 原位替换。后续若直接 append，场景就会同时出现
        # 在第一个分组和用户选择的目标分组，最终把非法重复项写回配置。
        #
        # 因此这里不能假设 _save_scene_yaml 前后的内存状态相同：先清掉热重载
        # 产生的临时注册，再按本次创建操作的唯一目标重建归属与顺序。
        self._scenes[key] = scene
        self._order = [existing for existing in self._order if existing != key]
        self._order.append(key)
        for scenes in self._group_scenes.values():
            scenes[:] = [existing for existing in scenes if existing != key]
        # 添加到指定分组（或第一个分组）
        target_group = group_key if group_key and group_key in self._groups else (self._group_order[0] if self._group_order else None)
        if target_group:
            self._group_scenes.setdefault(target_group, []).append(key)
        logger.info(f"已创建场景: {key} (分组={target_group})")
        return scene

    def delete_scene(self, key: str):
        """删除场景 YAML 文件并从注册表移除"""
        if key not in self._scenes:
            raise ValueError(f"场景不存在: {key}")
        owners = [s.key for s in self._scenes.values()
                  if any(r.scene == key for r in s.subscene_refs)]
        if owners:
            raise ValueError(f"场景 {key} 正被以下场景引用，不能删除: {', '.join(owners)}")
        self._resolver.delete_entity(f"scenes/{key}.yaml")
        del self._scenes[key]
        self._order.remove(key)
        # 从分组中移除
        group = self.get_scene_group(key)
        if group:
            self._group_scenes[group].remove(key)
        self._disabled_scenes.discard(key)
        logger.info(f"已删除场景: {key}")

    def rename_scene(self, key: str, new_key: str, new_name: str):
        """重命名场景（修改 YAML 的 key/name，必要时重命名文件）"""
        if key not in self._scenes:
            raise ValueError(f"场景不存在: {key}")
        _validate_key_and_name(new_key, new_name, tr("场景"))
        if new_key != key and new_key in self._scenes:
            raise ValueError(f"场景 key 已存在: {new_key}")
        if new_key != key:
            # 重命名是写新删旧；先鉴权，禁止在用户模式下为 system 场景
            # 创建孤立的新 key 影子文件。
            self._resolver.ensure_entity_deletable(f"scenes/{key}.yaml")
        scene = self._scenes[key]
        old_name = scene.name
        scene.key = new_key
        scene.name = new_name
        try:
            # 先写新文件，成功后再删除旧文件，避免写入失败时丢失原定义。
            self._save_scene_yaml(scene)
        except Exception:
            scene.key = key
            scene.name = old_name
            raise
        # 如果 key 变了，需要删旧建新
        if new_key != key:
            self._resolver.delete_entity(f"scenes/{key}.yaml")
            del self._scenes[key]
            self._scenes[new_key] = scene
            idx = self._order.index(key)
            self._order[idx] = new_key
            # 同步更新分组中的场景 key
            group = self.get_scene_group(key)
            if group:
                scenes_list = self._group_scenes[group]
                gidx = scenes_list.index(key)
                scenes_list[gidx] = new_key
            if key in self._disabled_scenes:
                self._disabled_scenes.remove(key)
                self._disabled_scenes.add(new_key)
            # 引用目标跟随场景 key 重命名。
            for owner in self._scenes.values():
                changed = False
                for ref in owner.subscene_refs:
                    if ref.scene == key:
                        ref.scene = new_key
                        changed = True
                if changed:
                    self._save_scene_yaml(owner)
        logger.info(f"已重命名场景: {key} -> {new_key}")

    def reorder_scenes(self, new_order: list[str]):
        """更新场景顺序（仅内存，不写文件）"""
        # 过滤掉不存在的 key，补充遗漏的 key
        self._order = [k for k in new_order if k in self._scenes]
        for k in self._scenes:
            if k not in self._order:
                self._order.append(k)

    def save_scene_order(self, order: list[str]):
        """将场景顺序写入 scenes.yaml（保持分组结构）"""
        self.reorder_scenes(order)
        # 同步更新各分组内的场景顺序
        for gk in self._group_order:
            group_scene_list = self._group_scenes.get(gk, [])
            # disabled 场景不出现在 UI 传回的 order 中；保留其原槽位，不能
            # 因为用户拖动可见 Tab 就把隐藏登记项从配置里删除。
            reordered = [k for k in order
                         if k in group_scene_list
                         and k not in self._disabled_scenes]
            reordered.extend(
                k for k in group_scene_list
                if k not in self._disabled_scenes and k not in reordered)
            visible = iter(reordered)
            new_list = [k if k in self._disabled_scenes else next(visible)
                        for k in group_scene_list]
            self._group_scenes[gk] = new_list
        self.save_group_config()

    # ─── 视图管理 ─────────────────────────────────────────────

    def get_scene_views(self, scene_key: str) -> list[ViewDef]:
        """获取场景的视图列表（空列表 = 未开启多视图）"""
        scene = self._scenes.get(scene_key)
        return list(scene.views) if scene else []

    def enable_scene_views(self, scene_key: str) -> list[ViewDef]:
        """把场景改为多视图：生成基底视图，已有定义全部归属基底（view 留空即基底）"""
        scene = self._require_scene(scene_key)
        if scene.is_subscene:
            raise ValueError("子场景不能启用多视图")
        if scene.views:
            return list(scene.views)
        scene.views = [ViewDef(key=BASE_VIEW_KEY, name=BASE_VIEW_NAME)]
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 已开启多视图")
        return list(scene.views)

    def disable_scene_views(self, scene_key: str):
        """取消多视图（仅剩基底视图时可用）"""
        scene = self._require_scene(scene_key)
        if len(scene.views) > 1:
            raise ValueError(f"仍存在 {len(scene.views) - 1} 个非基底视图，无法取消多视图")
        scene.views = []
        for item in (*scene.regions, *scene.points, *scene.panels,
                     *scene.subscene_refs, *scene.references):
            item.views = []
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 已取消多视图")

    def add_scene_view(self, scene_key: str, view_key: str, view_name: str) -> ViewDef:
        """新增视图（未开启多视图时自动先开启）"""
        scene = self._require_scene(scene_key)
        if view_key == BASE_VIEW_KEY:
            raise ValueError(f"{BASE_VIEW_KEY} 是基底视图的保留 key")
        if not scene.views:
            self.enable_scene_views(scene_key)
        if any(v.key == view_key for v in scene.views):
            raise ValueError(f"视图 key 已存在: {view_key}")
        view = ViewDef(key=view_key, name=view_name)
        scene.views.append(view)
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 新增视图: {view_key} ({view_name})")
        return view

    def rename_scene_view(self, scene_key: str, view_key: str, new_name: str):
        """重命名视图名称（key 不变）"""
        scene = self._require_scene(scene_key)
        view = next((v for v in scene.views if v.key == view_key), None)
        if view is None:
            raise ValueError(f"视图不存在: {view_key}")
        view.name = new_name
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 视图重命名: {view_key} -> {new_name}")

    def rename_scene_view_key(self, scene_key: str, old_key: str, new_key: str, new_name: str):
        """重命名视图 key 和名称（需同步更新所有引用的 region/point/panel）"""
        scene = self._require_scene(scene_key)
        if old_key == new_key:
            # key 不变，只改名称
            self.rename_scene_view(scene_key, old_key, new_name)
            return
        # 检查新 key 是否已存在
        if any(v.key == new_key for v in scene.views):
            raise ValueError(f"视图 key 已存在: {new_key}")
        # 找到并更新视图
        view = next((v for v in scene.views if v.key == old_key), None)
        if view is None:
            raise ValueError(f"视图不存在: {old_key}")
        view.key = new_key
        view.name = new_name
        # 更新所有引用该视图的 region/point/panel
        for item in (*scene.regions, *scene.points, *scene.panels,
                     *scene.subscene_refs, *scene.references):
            # 多视图归属：逐条替换，同一实体可能同时属于被改名的视图和别的视图
            renamed = []
            for v in (item.views or [""]):
                # 空字符串等价于基底视图 key，需要同步更新
                if v == old_key or (old_key == BASE_VIEW_KEY and v == ""):
                    v = new_key
                if v and v not in renamed:
                    renamed.append(v)
            item.views = renamed
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 视图 key 重命名: {old_key} -> {new_key}")

    def move_scene_view(self, scene_key: str, view_key: str, direction: int):
        """调整视图顺序（direction: -1=上移, +1=下移）"""
        scene = self._require_scene(scene_key)
        idx = next((i for i, v in enumerate(scene.views) if v.key == view_key), -1)
        if idx < 0:
            raise ValueError(f"视图不存在: {view_key}")
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(scene.views):
            return  # 已在边界，不移动
        # 交换位置
        scene.views[idx], scene.views[new_idx] = scene.views[new_idx], scene.views[idx]
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 视图顺序调整: {view_key} {'上移' if direction < 0 else '下移'}")

    def save_scene_views(self, scene_key: str):
        """视图属性（如同层标记）改动后落盘。"""
        scene = self._require_scene(scene_key)
        self._save_scene_yaml(scene)

    def delete_scene_view(self, scene_key: str, view_key: str):
        """删除空视图（视图下仍有定义时抛异常，基底视图不可删）"""
        scene = self._require_scene(scene_key)
        if view_key == BASE_VIEW_KEY:
            raise ValueError(tr("基底视图不可删除，如需取消分层请取消多视图"))
        if not any(v.key == view_key for v in scene.views):
            raise ValueError(f"视图不存在: {view_key}")
        used = [
            i.key for i in (*scene.regions, *scene.points, *scene.panels,
                            *scene.subscene_refs, *scene.references)
            if view_key in (i.views or [])
        ]
        if used:
            raise ValueError(f"视图非空，无法删除: {view_key}（包含 {len(used)} 个定义）")
        scene.views = [v for v in scene.views if v.key != view_key]
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} 已删除视图: {view_key}")

    def _require_scene(self, scene_key: str) -> SceneDef:
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        return scene

    def set_scene_type(self, scene_key: str, scene_type: str) -> None:
        """切换普通场景/子场景；子场景禁止多视图和嵌套引用。"""
        scene = self._require_scene(scene_key)
        if scene_type not in ("scene", "subscene"):
            raise ValueError("场景类型必须为 scene 或 subscene")
        if scene_type == "subscene":
            if scene.views:
                raise ValueError("多视图场景不能转为子场景，请先取消多视图")
            if scene.subscene_refs:
                raise ValueError("包含子场景引用的场景不能转为子场景")
        elif scene.is_subscene:
            owners = [s.key for s in self._scenes.values()
                      if any(r.scene == scene_key for r in s.subscene_refs)]
            if owners:
                raise ValueError(
                    f"该子场景仍被以下场景引用，不能取消: {', '.join(owners)}")
        scene.type = scene_type
        self._save_scene_yaml(scene)

    def add_subscene_ref_to_scene(self, scene_key: str,
                                  ref_def: SubsceneRefDef) -> None:
        scene = self._require_scene(scene_key)
        if scene.is_subscene:
            raise ValueError("子场景不能嵌套引用")
        target = self._require_scene(ref_def.scene)
        if not target.is_subscene:
            raise ValueError(f"引用目标不是子场景: {ref_def.scene}")
        self._check_key_unique(scene, ref_def.key)
        scene.subscene_refs.append(ref_def)
        self._save_scene_yaml(scene)

    def update_subscene_ref_in_scene(self, scene_key: str, old_key: str,
                                     ref_def: SubsceneRefDef) -> None:
        scene = self._require_scene(scene_key)
        target = self._require_scene(ref_def.scene)
        if not target.is_subscene:
            raise ValueError(f"引用目标不是子场景: {ref_def.scene}")
        if old_key != ref_def.key:
            self._check_key_unique_excluding(scene, ref_def.key, old_key)
        for index, current in enumerate(scene.subscene_refs):
            if current.key == old_key:
                scene.subscene_refs[index] = ref_def
                self._save_scene_yaml(scene)
                return
        raise ValueError(f"子场景引用不存在: {old_key}")

    def remove_subscene_ref_from_scene(self, scene_key: str, ref_key: str) -> None:
        scene = self._require_scene(scene_key)
        scene.subscene_refs = [r for r in scene.subscene_refs if r.key != ref_key]
        self._save_scene_yaml(scene)

    # ─── 区域/坐标 编辑 ───────────────────────────────────────

    def add_region_to_scene(self, scene_key: str, region_def: RegionDef):
        """向场景 YAML 追加 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, region_def.key)
        scene.regions.append(region_def)
        self._save_scene_yaml(scene)

    def add_scene_reference(self, scene_key: str, ref: SceneRefDef):
        """向场景追加一条跨场景 area 引用。

        坐标不复制：布局加载时从源场景转读。因此引用项在本场景**只读**，
        编辑器只提供新增/移除，改坐标要去源场景。
        """
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        if scene.is_subscene:
            raise ValueError(f"子场景 {scene_key} 不能引用其他场景的 area")
        source = self._scenes.get(ref.scene)
        if source is None:
            raise ValueError(f"源场景不存在: {ref.scene}")
        if source.is_subscene:
            raise ValueError("只能引用一级场景，不能引用子场景")
        if ref.scene == scene_key:
            raise ValueError("不能引用自身")
        if any(r.entity == ref.entity for r in source.references):
            raise ValueError("不能引用引用，源必须是原生定义")
        native = ([r.key for r in source.regions] + [p.key for p in source.points]
                  + [p.key for p in source.panels])
        if ref.entity not in native:
            raise ValueError(f"源场景 {ref.scene} 没有实体 {ref.entity}")
        self._check_key_unique(scene, ref.key)
        scene.references.append(ref)
        self._save_scene_yaml(scene)

    def remove_scene_reference(self, scene_key: str, source_scene: str,
                               entity: str):
        """移除一条跨场景引用；源场景的定义不受影响。"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        scene.references = [
            r for r in scene.references
            if not (r.scene == source_scene and r.entity == entity)]
        self._save_scene_yaml(scene)

    def update_scene_reference_views(self, scene_key: str, source_scene: str,
                                     entity: str, views: list[str]):
        """改一条引用在**本场景**的归属视图。

        引用项本身是只读的（坐标、类型、名字都在源场景），但「在本场景的
        哪些视图下看得见」是本场景自己的数据，理应能改——否则加错视图之后
        只能删掉重加。
        """
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        for ref in scene.references:
            if ref.scene == source_scene and ref.entity == entity:
                ref.views = list(views)
                break
        else:
            raise ValueError(f"引用不存在: {source_scene}.{entity}")
        self._save_scene_yaml(scene)

    def find_references_to(self, scene_key: str,
                           entity: str) -> list[str]:
        """哪些场景引用了 ``scene_key.entity``，返回场景 key 列表。"""
        return [
            key for key, scene in self._scenes.items()
            if any(r.scene == scene_key and r.entity == entity
                   for r in scene.references)
        ]

    def _reject_if_referenced(self, scene_key: str, entity: str) -> None:
        """有别的场景引用它就不许删。

        引用只存 ``(源场景, 实体名)``，坐标运行期从源场景转读。源定义没了，
        引用就成了悬空声明：加载期会在内存里被丢掉，于是那个场景**静静少
        了一个实体**，直到某条 .wf 跑到 ``click [场景].[实体]`` 才炸。

        级联删除更糟——用户在 A 场景点了删除，B 场景的定义跟着没了，而他
        根本不知道 B 引用过它。所以这里拦下来，把引用方报出来，让人自己
        决定是先去解引用还是不删。
        """
        referrers = self.find_references_to(scene_key, entity)
        if not referrers:
            return
        names = "、".join(
            f"{self._scenes[key].name}({key})" for key in referrers)
        raise ValueError(
            f"{entity} 正被 {len(referrers)} 个场景引用，不能删除：{names}。"
            f"请先在这些场景里移除引用。")

    def retarget_references(self, old_scene: str, new_scene: str,
                            entity: str, new_entity: str = "") -> list[str]:
        """源实体搬了家或改了名，把指向它的引用一并改指过去。

        规则是「引用跟着源实体走，源实体没了才拦」。改名和跨场景迁移都不是
        删除——实体还在，只是换了地址；不改指的话引用就悬空了，而且
        :meth:`_reject_if_referenced` 会把迁移一起拦下来，拦它没道理。

        返回改动过的场景 key 列表。
        """
        target_entity = new_entity or entity
        if (old_scene, entity) == (new_scene, target_entity):
            return []
        changed = []
        for key in self.find_references_to(old_scene, entity):
            scene = self._scenes[key]
            for ref in scene.references:
                if ref.scene == old_scene and ref.entity == entity:
                    ref.scene = new_scene
                    ref.entity = target_entity
            self._save_scene_yaml(scene)
            changed.append(key)
        return changed

    def remove_region_from_scene(self, scene_key: str, region_key: str):
        """从场景 YAML 移除 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._reject_if_referenced(scene_key, region_key)
        scene.regions = [r for r in scene.regions if r.key != region_key]
        self._save_scene_yaml(scene)

    def update_region_in_scene(self, scene_key: str, old_key: str, region_def: RegionDef):
        """更新场景中的 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        for i, r in enumerate(scene.regions):
            if r.key == old_key:
                scene.regions[i] = region_def
                break
        else:
            raise ValueError(f"区域不存在: {old_key}")
        self._save_scene_yaml(scene)

    def add_point_to_scene(self, scene_key: str, point_def: PointDef):
        """向场景 YAML 追加 point 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, point_def.key)
        scene.points.append(point_def)
        self._save_scene_yaml(scene)

    def remove_point_from_scene(self, scene_key: str, point_key: str):
        """从场景 YAML 移除 point 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._reject_if_referenced(scene_key, point_key)
        scene.points = [p for p in scene.points if p.key != point_key]
        self._save_scene_yaml(scene)

    def update_point_in_scene(self, scene_key: str, old_key: str, point_def: PointDef):
        """更新场景中的 point 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        for i, p in enumerate(scene.points):
            if p.key == old_key:
                scene.points[i] = point_def
                break
        else:
            raise ValueError(f"坐标点不存在: {old_key}")
        self._save_scene_yaml(scene)

    def add_panel_to_scene(self, scene_key: str, panel_def: PanelDef):
        """向场景 YAML 追加 panel 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, panel_def.key)
        scene.panels.append(panel_def)
        self._save_scene_yaml(scene)

    def remove_panel_from_scene(self, scene_key: str, panel_key: str):
        """从场景 YAML 移除 panel 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._reject_if_referenced(scene_key, panel_key)
        scene.panels = [p for p in scene.panels if p.key != panel_key]
        self._save_scene_yaml(scene)

    def update_panel_in_scene(self, scene_key: str, old_key: str, panel_def: PanelDef):
        """更新场景中的 panel 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        for i, p in enumerate(scene.panels):
            if p.key == old_key:
                scene.panels[i] = panel_def
                break
        else:
            raise ValueError(f"面板不存在: {old_key}")
        self._save_scene_yaml(scene)

    def reorder_scene_entities(self, scene_key: str, kind: str,
                               ordered_keys: list[str]) -> bool:
        """按 key 重排一种场景实体并写回 YAML。

        ``ordered_keys`` 可以只是当前视图可见的子集；这些实体只在它们原来
        占据的槽位间换序，未显示的定义保持原槽位。返回是否实际发生变化。
        """
        if kind not in {"regions", "points", "panels", "subscene_refs"}:
            raise ValueError(f"不支持排序的场景实体类型: {kind}")
        if len(ordered_keys) != len(set(ordered_keys)):
            raise ValueError("实体排序中存在重复 key")

        scene = self._require_scene(scene_key)
        entities = list(getattr(scene, kind))
        existing = {item.key for item in entities}
        unknown = [key for key in ordered_keys if key not in existing]
        if unknown:
            raise ValueError(f"实体排序包含未知 key: {unknown}")
        if len(ordered_keys) < 2:
            return False

        selected = set(ordered_keys)
        replacements = iter(ordered_keys)
        reordered = [
            next(replacements) if item.key in selected else item.key
            for item in entities
        ]
        current = [item.key for item in entities]
        if reordered == current:
            return False

        by_key = {item.key: item for item in entities}
        setattr(scene, kind, [by_key[key] for key in reordered])
        self._save_scene_yaml(scene)
        logger.info(f"场景 {scene_key} {kind} 定义顺序已更新: {ordered_keys}")
        return True

    def rename_region_key(self, scene_key: str, old_key: str, new_key: str):
        """重命名场景内 region 的 key（region/point/panel 共享命名空间）"""
        scene = self._require_scene(scene_key)
        if old_key == new_key:
            return
        # 查找 region
        region = next((r for r in scene.regions if r.key == old_key), None)
        if region is None:
            raise ValueError(f"区域不存在: {old_key}")
        # 校验新 key 唯一性（排除自身）
        self._check_key_unique_excluding(scene, new_key, old_key)
        region.key = new_key
        self._save_scene_yaml(scene)
        # 引用按 (源场景, 实体 key) 定位，改名不跟着走就成了悬空声明
        self.retarget_references(scene_key, scene_key, old_key, new_key)
        logger.info(f"场景 {scene_key} region key 重命名: {old_key} -> {new_key}")

    def rename_point_key(self, scene_key: str, old_key: str, new_key: str):
        """重命名场景内 point 的 key（region/point/panel 共享命名空间）"""
        scene = self._require_scene(scene_key)
        if old_key == new_key:
            return
        # 查找 point
        point = next((p for p in scene.points if p.key == old_key), None)
        if point is None:
            raise ValueError(f"坐标点不存在: {old_key}")
        # 校验新 key 唯一性（排除自身）
        self._check_key_unique_excluding(scene, new_key, old_key)
        point.key = new_key
        self._save_scene_yaml(scene)
        # 引用按 (源场景, 实体 key) 定位，改名不跟着走就成了悬空声明
        self.retarget_references(scene_key, scene_key, old_key, new_key)
        logger.info(f"场景 {scene_key} point key 重命名: {old_key} -> {new_key}")

    def rename_panel_key(self, scene_key: str, old_key: str, new_key: str):
        """重命名场景内 panel 的 key（region/point/panel 共享命名空间）"""
        scene = self._require_scene(scene_key)
        if old_key == new_key:
            return
        # 查找 panel
        panel = next((p for p in scene.panels if p.key == old_key), None)
        if panel is None:
            raise ValueError(f"面板不存在: {old_key}")
        # 校验新 key 唯一性（排除自身）
        self._check_key_unique_excluding(scene, new_key, old_key)
        panel.key = new_key
        self._save_scene_yaml(scene)
        # 引用按 (源场景, 实体 key) 定位，改名不跟着走就成了悬空声明
        self.retarget_references(scene_key, scene_key, old_key, new_key)
        logger.info(f"场景 {scene_key} panel key 重命名: {old_key} -> {new_key}")

    # ─── 内部方法 ─────────────────────────────────────────────

    def _check_key_unique(self, scene: SceneDef, new_key: str):
        """检查 key 在场景内是否重复（region/point/panel 共享命名空间）"""
        all_keys = (
            [r.key for r in scene.regions]
            + [p.key for p in scene.points]
            + [p.key for p in scene.panels]
            + [r.key for r in scene.subscene_refs]
        )
        if new_key in all_keys:
            raise ValueError(f"key 已存在: {new_key}")

    def _check_key_unique_excluding(self, scene: SceneDef, new_key: str, exclude_key: str):
        """检查 key 在场景内是否重复，但排除指定的旧 key（用于重命名场景）"""
        all_keys = (
            [r.key for r in scene.regions if r.key != exclude_key]
            + [p.key for p in scene.points if p.key != exclude_key]
            + [p.key for p in scene.panels if p.key != exclude_key]
            + [r.key for r in scene.subscene_refs if r.key != exclude_key]
        )
        if new_key in all_keys:
            raise ValueError(f"key 已存在: {new_key}")

    def save_scene_content_version(self, scene_key: str,
                                   content_version: int) -> None:
        """按当前内存中的场景定义写入显式版本。

        场景编辑器把版本提升作为待保存状态；只有用户确认保存时才调用这里，
        因此关闭或切换时选择放弃不会改动磁盘版本。
        """
        scene = self._scenes.get(scene_key)
        if scene is None:
            raise ValueError(f"场景不存在: {scene_key}")
        self._save_scene_yaml(scene, content_version=content_version)

    def _save_scene_yaml(self, scene: SceneDef,
                         content_version: int | None = None):
        """将场景定义经 resolver 写入 YAML（开发→system/scenes，用户→local/scenes）"""
        data: dict[str, Any] = {"key": scene.key, "name": scene.name}
        if scene.type != "scene":
            data["type"] = scene.type
        if scene.views:
            data["views"] = [v.to_dict() for v in scene.views]
        if scene.regions:
            data["regions"] = [
                {
                    "key": r.key,
                    "name": r.name,
                    "type": r.type,
                    "is_text": r.is_text,
                    "is_clickable": r.is_clickable,
                    **_view_fields(r),
                    **({"to": r.to} if r.is_clickable and r.to else {}),
                }
                for r in scene.regions
            ]
        if scene.points:
            data["points"] = [
                {
                    "key": p.key,
                    "name": p.name,
                    "type": p.type,
                    "is_text": p.is_text,
                    "is_clickable": p.is_clickable,
                    **_view_fields(p),
                    **({"to": p.to} if p.is_clickable and p.to else {}),
                }
                for p in scene.points
            ]
        if scene.panels:
            data["panels"] = [
                {
                    "key": p.key,
                    "name": p.name,
                    "min_visible": p.min_visible,
                    **_view_fields(p),
                    **({"calibration": p.calibration} if p.calibration != "auto" else {}),
                    **({"scroll_direction": p.scroll_direction} if p.scroll_direction != "vertical" else {}),
                }
                for p in scene.panels
            ]
        if scene.subscene_refs:
            data["subscene_refs"] = [
                {"key": r.key, "name": r.name, "scene": r.scene,
                 **_view_fields(r)}
                for r in scene.subscene_refs
            ]
        if scene.references:
            data["references"] = [
                {"scene": r.scene, "entity": r.entity, **_view_fields(r)}
                for r in scene.references
            ]
        self._resolver.write_entity(
            f"scenes/{scene.key}.yaml",
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            content_version=content_version,
        )
