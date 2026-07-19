"""场景定义加载器 - 从 YAML 文件加载场景配置"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


# 合法的 region type 枚举
VALID_REGION_TYPES = {"attr", "slot", "func"}


@dataclass
class RegionDef:
    """单个区域的完整定义（场景内的一个可交互/可识别元素）"""
    key: str
    name: str
    type: str = "attr"                # attr/slot/func
    is_text: bool = True              # 是否需要文字识别（OCR）
    is_clickable: bool = False        # 是否可点击


@dataclass
class PointDef:
    """单个坐标点的类型定义（圆形交互锚点）

    描述「场景里存在这样一个可交互坐标点」的类型信息（key/name/type/is_text/is_clickable）。
    具体的坐标位置与半径属于实例数据，保存在布局 JSON 的 Point 中，
    半径可在画布上随意调整，不在此处限死。
    """
    key: str
    name: str
    type: str = "func"              # attr/slot/func
    is_text: bool = False           # 是否需要文字识别（OCR）
    is_clickable: bool = True       # 是否可点击


@dataclass
class SceneDef:
    """单个场景的完整定义"""
    key: str
    name: str
    regions: list[RegionDef] = field(default_factory=list)
    points: list[PointDef] = field(default_factory=list)


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
        scenes_dir: Path,
        scene_order: list[str] | None = None,
        group_config: dict[str, list[str]] | None = None,
        group_names: dict[str, str] | None = None,
    ):
        self._scenes_dir = scenes_dir
        self._scenes: dict[str, SceneDef] = {}
        self._order: list[str] = []
        # 分组数据
        self._groups: dict[str, str] = {}            # group_key -> group_name
        self._group_order: list[str] = []            # 分组顺序
        self._group_scenes: dict[str, list[str]] = {}  # group_key -> [scene_key, ...]
        self._load_all(scenes_dir, scene_order, group_config, group_names)

    def _load_all(
        self,
        scenes_dir: Path,
        scene_order: list[str] | None,
        group_config: dict[str, list[str]] | None = None,
        group_names: dict[str, str] | None = None,
    ):
        """加载场景：按 scene_order 指定的顺序，未指定的按文件名追加"""
        if not scenes_dir.exists():
            logger.warning(f"场景配置目录不存在: {scenes_dir}")
            return

        # 先扫描全部 YAML 文件，建立 key -> path 映射
        file_map: dict[str, Path] = {}
        for yaml_file in scenes_dir.glob("*.yaml"):
            try:
                scene = self._load_scene(yaml_file)
                file_map[scene.key] = yaml_file
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
            raise ValueError("YAML 顶层必须是字典")

        key = data.get("key")
        name = data.get("name")
        if not key or not name:
            raise ValueError("场景必须包含 key 和 name")

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
            ))

        # region 与 point 同场景内 key 不得重复（共享命名空间）
        all_keys = [r.key for r in regions] + [p.key for p in points]
        dup = {k for k in all_keys if all_keys.count(k) > 1}
        if dup:
            raise ValueError(f"场景 {key} 的 region/point key 重复: {dup}")

        return SceneDef(key=key, name=name, regions=regions, points=points)

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
        self._groups[key] = new_name
        logger.info(f"已重命名分组: {key} -> {new_name}")

    def delete_group(self, key: str):
        """删除空分组（非空抛异常）"""
        if key not in self._groups:
            raise ValueError(f"分组不存在: {key}")
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

    def save_group_config(self, scenes_config_path: Path):
        """将分组配置写入 scenes.yaml"""
        data = {}
        if scenes_config_path.exists():
            try:
                data = yaml.safe_load(scenes_config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
        # 构建分组结构
        layout_scenes = {}
        for gk in self._group_order:
            layout_scenes[gk] = self._group_scenes.get(gk, [])
        data["layout_scenes"] = layout_scenes
        data["group_names"] = dict(self._groups)
        scenes_config_path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info(f"已保存分组配置: {self._group_order}")

    # ─── 场景 CRUD ──────────────────────────────────────────

    def create_scene(self, key: str, name: str, group_key: str | None = None) -> SceneDef:
        """创建新场景 YAML 文件并注册，可选指定分组"""
        if key in self._scenes:
            raise ValueError(f"场景 key 已存在: {key}")
        scene = SceneDef(key=key, name=name)
        yaml_path = self._scenes_dir / f"{key}.yaml"
        self._save_scene_yaml(yaml_path, scene)
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
        yaml_path = self._scenes_dir / f"{key}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()
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
        if new_key != key and new_key in self._scenes:
            raise ValueError(f"场景 key 已存在: {new_key}")
        scene = self._scenes[key]
        scene.key = new_key
        scene.name = new_name
        # 如果 key 变了，需要重命名文件
        if new_key != key:
            old_path = self._scenes_dir / f"{key}.yaml"
            new_path = self._scenes_dir / f"{new_key}.yaml"
            if old_path.exists():
                old_path.rename(new_path)
            del self._scenes[key]
            self._scenes[new_key] = scene
            idx = self._order.index(key)
            self._order[idx] = new_key
        self._save_scene_yaml(self._scenes_dir / f"{new_key}.yaml", scene)
        logger.info(f"已重命名场景: {key} -> {new_key}")

    def reorder_scenes(self, new_order: list[str]):
        """更新场景顺序（仅内存，不写文件）"""
        # 过滤掉不存在的 key，补充遗漏的 key
        self._order = [k for k in new_order if k in self._scenes]
        for k in self._scenes:
            if k not in self._order:
                self._order.append(k)

    def save_scene_order(self, order: list[str], scenes_config_path: Path):
        """将场景顺序写入 scenes.yaml（保持分组结构）"""
        self.reorder_scenes(order)
        # 同步更新各分组内的场景顺序
        for gk in self._group_order:
            group_scene_list = self._group_scenes.get(gk, [])
            # 按 order 中的顺序重新排列该分组的场景
            new_list = [k for k in order if k in group_scene_list]
            self._group_scenes[gk] = new_list
        self.save_group_config(scenes_config_path)

    # ─── 区域/坐标 编辑 ───────────────────────────────────────

    def add_region_to_scene(self, scene_key: str, region_def: RegionDef):
        """向场景 YAML 追加 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, region_def.key)
        scene.regions.append(region_def)
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

    def remove_region_from_scene(self, scene_key: str, region_key: str):
        """从场景 YAML 移除 region 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        scene.regions = [r for r in scene.regions if r.key != region_key]
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

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
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

    def add_point_to_scene(self, scene_key: str, point_def: PointDef):
        """向场景 YAML 追加 point 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        self._check_key_unique(scene, point_def.key)
        scene.points.append(point_def)
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

    def remove_point_from_scene(self, scene_key: str, point_key: str):
        """从场景 YAML 移除 point 定义"""
        scene = self._scenes.get(scene_key)
        if not scene:
            raise ValueError(f"场景不存在: {scene_key}")
        scene.points = [p for p in scene.points if p.key != point_key]
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

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
        self._save_scene_yaml(self._scenes_dir / f"{scene_key}.yaml", scene)

    # ─── 内部方法 ─────────────────────────────────────────────

    def _check_key_unique(self, scene: SceneDef, new_key: str):
        """检查 key 在场景内是否重复"""
        all_keys = [r.key for r in scene.regions] + [p.key for p in scene.points]
        if new_key in all_keys:
            raise ValueError(f"key 已存在: {new_key}")

    def _save_scene_yaml(self, path: Path, scene: SceneDef):
        """将场景定义写入 YAML 文件"""
        data = {"key": scene.key, "name": scene.name}
        if scene.regions:
            data["regions"] = [
                {
                    "key": r.key,
                    "name": r.name,
                    "type": r.type,
                    "is_text": r.is_text,
                    "is_clickable": r.is_clickable,
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
                }
                for p in scene.points
            ]
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
