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
from .scene_definition_models import (
    BASE_VIEW_KEY,
    BASE_VIEW_NAME,
    VALID_REGION_TYPES,
    PanelDef,
    PointDef,
    RegionDef,
    SceneDef,
    ViewDef,
)

_VALID_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_key_and_name(key: str, name: str, kind: str) -> None:
    if not _VALID_KEY_RE.fullmatch(key):
        raise ValueError(f"{kind} key 必须以小写字母开头，仅含小写字母/数字/下划线")
    if not name.strip():
        raise ValueError(f"{kind}名称不能为空")


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
    ):
        self._resolver = resolver or get_resolver()
        self._scenes: dict[str, SceneDef] = {}
        self._order: list[str] = []
        # 分组数据
        self._groups: dict[str, str] = {}            # group_key -> group_name
        self._group_order: list[str] = []            # 分组顺序
        self._group_scenes: dict[str, list[str]] = {}  # group_key -> [scene_key, ...]
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
            views.append(ViewDef(key=vd["key"], name=vd.get("name", vd["key"])))
        view_keys = {v.key for v in views}

        def _view_of(d: dict) -> str:
            """读取并校验定义的归属视图（未开启多视图时一律视为基底）"""
            v = d.get("view", "") or ""
            if not views or v in ("", BASE_VIEW_KEY):
                return "" if not views else v
            if v not in view_keys:
                raise ValueError(
                    f"定义 {d.get('key')} 的 view '{v}' 未在场景 views 中声明"
                )
            return v

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
                view=_view_of(rd),
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
                view=_view_of(pd),
            ))

        panels = []
        for pd in data.get("panels", []):
            panels.append(PanelDef(
                key=pd["key"],
                name=pd.get("name", pd["key"]),
                min_visible=float(pd.get("min_visible", 0.95)),
                view=_view_of(pd),
                calibration=str(pd.get("calibration", "auto")),
                scroll_direction=str(pd.get("scroll_direction", "vertical")),
            ))

        # region 与 point 同场景内 key 不得重复（共享命名空间）
        all_keys = [r.key for r in regions] + [p.key for p in points] + [p.key for p in panels]
        dup = {k for k in all_keys if all_keys.count(k) > 1}
        if dup:
            raise ValueError(f"场景 {key} 的 region/point/panel key 重复: {dup}")

        return SceneDef(key=key, name=name, regions=regions, points=points,
                        panels=panels, views=views)

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
        """获取分组下的场景 key 列表"""
        return list(self._group_scenes.get(group_key, []))

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
        """将分组配置写入 scenes.yaml（聚合键值文件，经 resolver 读合并视图、按模式写回）"""
        data = self._resolver.load_merged("scenes.yaml")
        # 构建分组结构
        layout_scenes = {}
        for gk in self._group_order:
            layout_scenes[gk] = self._group_scenes.get(gk, [])
        data["layout_scenes"] = layout_scenes
        data["group_names"] = dict(self._groups)
        self._resolver.save_merged("scenes.yaml", data)
        logger.info(f"已保存分组配置: {self._group_order}")

    # ─── 场景 CRUD ──────────────────────────────────────────

    def create_scene(self, key: str, name: str, group_key: str | None = None) -> SceneDef:
        """创建新场景 YAML 文件并注册，可选指定分组"""
        _validate_key_and_name(key, name, tr("场景"))
        if key in self._scenes:
            raise ValueError(f"场景 key 已存在: {key}")
        scene = SceneDef(key=key, name=name)
        self._save_scene_yaml(scene)
        self._scenes[key] = scene
        self._order.append(key)
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
        self._resolver.delete_entity(f"scenes/{key}.yaml")
        del self._scenes[key]
        self._order.remove(key)
        # 从分组中移除
        group = self.get_scene_group(key)
        if group:
            self._group_scenes[group].remove(key)
        logger.info(f"已删除场景: {key}")

    def rename_scene(self, key: str, new_key: str, new_name: str):
        """重命名场景（修改 YAML 的 key/name，必要时重命名文件）"""
        if key not in self._scenes:
            raise ValueError(f"场景不存在: {key}")
        _validate_key_and_name(new_key, new_name, tr("场景"))
        if new_key != key and new_key in self._scenes:
            raise ValueError(f"场景 key 已存在: {new_key}")
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
            # 按 order 中的顺序重新排列该分组的场景
            new_list = [k for k in order if k in group_scene_list]
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
        for item in (*scene.regions, *scene.points, *scene.panels):
            item.view = ""
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
        for item in (*scene.regions, *scene.points, *scene.panels):
            if item.view == old_key:
                item.view = new_key
            # 空字符串等价于基底视图 key，需要同步更新
            elif old_key == BASE_VIEW_KEY and item.view == "":
                item.view = new_key
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

    def delete_scene_view(self, scene_key: str, view_key: str):
        """删除空视图（视图下仍有定义时抛异常，基底视图不可删）"""
        scene = self._require_scene(scene_key)
        if view_key == BASE_VIEW_KEY:
            raise ValueError(tr("基底视图不可删除，如需取消分层请取消多视图"))
        if not any(v.key == view_key for v in scene.views):
            raise ValueError(f"视图不存在: {view_key}")
        used = [
            i.key for i in (*scene.regions, *scene.points, *scene.panels)
            if i.view == view_key
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

    # ─── 区域/坐标 编辑 ───────────────────────────────────────

    def add_region_to_scene(self, scene_key: str, region_def: RegionDef):
        """向场景 YAML 追加 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, region_def.key)
        scene.regions.append(region_def)
        self._save_scene_yaml(scene)

    def remove_region_from_scene(self, scene_key: str, region_key: str):
        """从场景 YAML 移除 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
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
        logger.info(f"场景 {scene_key} panel key 重命名: {old_key} -> {new_key}")

    # ─── 内部方法 ─────────────────────────────────────────────

    def _check_key_unique(self, scene: SceneDef, new_key: str):
        """检查 key 在场景内是否重复（region/point/panel 共享命名空间）"""
        all_keys = (
            [r.key for r in scene.regions]
            + [p.key for p in scene.points]
            + [p.key for p in scene.panels]
        )
        if new_key in all_keys:
            raise ValueError(f"key 已存在: {new_key}")

    def _check_key_unique_excluding(self, scene: SceneDef, new_key: str, exclude_key: str):
        """检查 key 在场景内是否重复，但排除指定的旧 key（用于重命名场景）"""
        all_keys = (
            [r.key for r in scene.regions if r.key != exclude_key]
            + [p.key for p in scene.points if p.key != exclude_key]
            + [p.key for p in scene.panels if p.key != exclude_key]
        )
        if new_key in all_keys:
            raise ValueError(f"key 已存在: {new_key}")

    def _save_scene_yaml(self, scene: SceneDef):
        """将场景定义经 resolver 写入 YAML（开发→system/scenes，用户→local/scenes）"""
        data: dict[str, Any] = {"key": scene.key, "name": scene.name}
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
                    **({"view": r.view} if r.view else {}),
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
                    **({"view": p.view} if p.view else {}),
                }
                for p in scene.points
            ]
        if scene.panels:
            data["panels"] = [
                {
                    "key": p.key,
                    "name": p.name,
                    "min_visible": p.min_visible,
                    **({"view": p.view} if p.view else {}),
                    **({"calibration": p.calibration} if p.calibration != "auto" else {}),
                    **({"scroll_direction": p.scroll_direction} if p.scroll_direction != "vertical" else {}),
                }
                for p in scene.panels
            ]
        self._resolver.write_entity(
            f"scenes/{scene.key}.yaml",
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        )
