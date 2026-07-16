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
class SceneDef:
    """单个场景的完整定义"""
    key: str
    name: str
    regions: list[RegionDef] = field(default_factory=list)


# 向后兼容别名（外部代码若仍用 FieldDef 可继续 import）
FieldDef = RegionDef


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
    """

    def __init__(self, scenes_dir: Path, scene_order: list[str] | None = None):
        self._scenes: dict[str, SceneDef] = {}
        self._order: list[str] = []
        self._load_all(scenes_dir, scene_order)

    def _load_all(self, scenes_dir: Path, scene_order: list[str] | None):
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

        return SceneDef(key=key, name=name, regions=regions)

    def get_scene(self, key: str) -> SceneDef | None:
        """获取场景定义，不存在返回 None"""
        return self._scenes.get(key)

    def all_scene_keys(self) -> list[str]:
        """返回所有已注册的场景 key 列表（按配置顺序）"""
        return list(self._order)

    def all_scenes(self) -> dict[str, SceneDef]:
        """返回所有场景的 {key: SceneDef} 字典（按配置顺序）"""
        return {k: self._scenes[k] for k in self._order}
