"""地图定义（世界系）与地图管理

**地图**描述一个玩法的可导航世界：底图、底图归一化坐标下的兴趣点（POI）、
导航模式，以及它在屏幕上的入口——这些入口只引用界面场景实体 key，屏幕坐标
永远在布局层，由场景编辑器标定。

每张地图对应四类文件（均在 system/local/remote 三层内，规则同其它实体）：

    maps/{key}.yaml                  地图定义（本模块读写）
    maps/{key}/base.png              底图
    scenes/map_{key}.yaml            该地图专属 HUD 场景（创建地图时自动生成）
    layouts/{layout}/map_{key}.json  各布局坐标（场景编辑器标定，本模块不碰）

小地图区域**随地图走**而不是指向某个固定场景：每个玩法的 HUD 布局都可能
不同，所以每张地图自带一个 ``map_{key}`` 场景，里面是固定的实体集合
（见 :data:`HUD_SCENE_ENTITIES`）。两个玩法 HUD 完全一样时，``ui.scene``
可以指向同一个场景，不强制复制。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ..i18n import tr
from .config.resolver import LAYER_LOCAL, LAYER_REMOTE, ConfigResolver, get_resolver
from .config.versioning import register_versioned_dir
from .scene_definition_models import PointDef, RegionDef

MAPS_DIR = "maps"
BASE_IMAGE_NAME = "base.png"
HUD_SCENE_PREFIX = "map_"
HUD_SCENE_GROUP_KEY = "map"
HUD_SCENE_GROUP_NAME = "地图"
FULL_VIEW_KEY = "full"

NAV_MODE_PATHFIND = "pathfind"
NAV_MODE_CLOSED_LOOP = "closed_loop"
NAV_MODES = (NAV_MODE_PATHFIND, NAV_MODE_CLOSED_LOOP)

#: POI 种类：exit 撤离点 / spawn 出生点 / poi 普通兴趣点。kind 只是标签，
#: 导航策略按 kind 选目标，不在这里限定枚举。
DEFAULT_POI_KIND = "poi"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

register_versioned_dir(MAPS_DIR, "*.yaml", depth=1)

#: HUD 场景的固定实体集：(视图, 类别, key, 名称, 可点击)。
#: - ``minimap``：小地图可视区域，箭头解析的搜索窗口
#: - ``minimap_center``：箭头中心（永远居中，显式标一次比取区域中心稳）
#: - ``open_map``：打开大地图。安卓覆盖在小地图上；桌面靠快捷键时不标坐标、
#:   只绑 activation_key（布局支持无坐标绑定）
#: - ``full_map``：大地图可视区域，底图与它对齐（``full`` 视图）
#: - ``close_map``：关闭大地图（``full`` 视图）
HUD_SCENE_ENTITIES: tuple[tuple[str, str, str, str, bool], ...] = (
    ("", "region", "minimap", "小地图", False),
    ("", "point", "minimap_center", "小地图中心", False),
    ("", "region", "open_map", "打开地图", True),
    (FULL_VIEW_KEY, "region", "full_map", "大地图", False),
    (FULL_VIEW_KEY, "region", "close_map", "关闭地图", True),
)


class MapError(ValueError):
    """地图定义不合法或操作不被允许。"""


@dataclass
class MapPoi:
    key: str
    x: float
    y: float
    kind: str = DEFAULT_POI_KIND
    name: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"key": self.key, "kind": self.kind}
        if self.name:
            d["name"] = self.name
        d["x"] = round(float(self.x), 6)
        d["y"] = round(float(self.y), 6)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> MapPoi:
        if not isinstance(d, dict):
            raise MapError(tr("POI 必须是键值映射"))
        return cls(
            key=str(d["key"]),
            x=float(d["x"]),
            y=float(d["y"]),
            kind=str(d.get("kind") or DEFAULT_POI_KIND),
            name=str(d.get("name") or ""),
        )


@dataclass
class MapUiBinding:
    """地图在屏幕上的入口：只存场景与实体 key，坐标在布局层。"""

    scene: str
    minimap: str = "minimap"
    minimap_center: str = "minimap_center"
    open_map: str = "open_map"
    full_map: str = "full_map"
    close_map: str = "close_map"
    generated_scene: bool = False

    def to_dict(self) -> dict:
        return {
            "scene": self.scene,
            "minimap": self.minimap,
            "minimap_center": self.minimap_center,
            "open_map": self.open_map,
            "full_map": self.full_map,
            "close_map": self.close_map,
            **({"generated_scene": True} if self.generated_scene else {}),
        }

    @classmethod
    def from_dict(cls, d: dict, default_scene: str) -> MapUiBinding:
        if not isinstance(d, dict):
            raise MapError(tr("ui 必须是键值映射"))
        return cls(
            scene=str(d.get("scene") or default_scene),
            minimap=str(d.get("minimap") or "minimap"),
            minimap_center=str(d.get("minimap_center") or "minimap_center"),
            open_map=str(d.get("open_map") or "open_map"),
            full_map=str(d.get("full_map") or "full_map"),
            close_map=str(d.get("close_map") or "close_map"),
            generated_scene=bool(d.get("generated_scene", False)),
        )


HEADING_METHOD_CONCAVE_ARROW = "concave_arrow"


@dataclass
class HeadingDetector:
    """小地图朝向箭头的解析参数（见 ``recognizers.heading``）。

    HSV 阈值按 OpenCV 约定（H 0–180）；``window_ratio`` 是搜索窗口边长相对
    小地图短边的比例。
    """

    method: str = HEADING_METHOD_CONCAVE_ARROW
    hsv_lower: tuple[int, int, int] = (18, 90, 120)
    hsv_upper: tuple[int, int, int] = (42, 255, 255)
    window_ratio: float = 0.5
    min_area: int = 12

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "hsv_lower": [int(v) for v in self.hsv_lower],
            "hsv_upper": [int(v) for v in self.hsv_upper],
            "window_ratio": float(self.window_ratio),
            "min_area": int(self.min_area),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> HeadingDetector:
        d = d or {}

        def triple(name: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
            raw = d.get(name)
            if raw is None:
                return default
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                raise MapError(tr("{name} 必须是三元整数列表").format(name=name))
            values = tuple(int(v) for v in raw)
            if not (0 <= values[0] <= 180
                    and all(0 <= value <= 255 for value in values[1:])):
                raise MapError(
                    tr("{name} 必须满足 H=0–180、S/V=0–255").format(name=name))
            return values  # type: ignore[return-value]

        window_ratio = float(d.get("window_ratio", 0.5))
        if not 0.05 <= window_ratio <= 1.0:
            raise MapError(tr("window_ratio 必须在 0.05–1 之间"))
        method = str(d.get("method") or HEADING_METHOD_CONCAVE_ARROW)
        if method != HEADING_METHOD_CONCAVE_ARROW:
            raise MapError(tr("未知朝向识别方法: {method}").format(method=method))
        min_area = int(d.get("min_area", 12))
        if min_area < 1:
            raise MapError(tr("min_area 必须大于 0"))
        return cls(
            method=method,
            hsv_lower=triple("hsv_lower", (18, 90, 120)),
            hsv_upper=triple("hsv_upper", (42, 255, 255)),
            window_ratio=window_ratio,
            min_area=min_area,
        )


@dataclass
class MapDef:
    key: str
    name: str
    navigation_mode: str = NAV_MODE_CLOSED_LOOP
    north_up: bool = True
    base_image: str = BASE_IMAGE_NAME
    base_image_sha256: str = ""
    ui: MapUiBinding = field(default_factory=lambda: MapUiBinding(scene=""))
    pois: list[MapPoi] = field(default_factory=list)
    heading: HeadingDetector = field(default_factory=HeadingDetector)
    #: 实际生效的来源层：system / local / remote（读取时填充，不序列化）
    layer: str = ""

    @property
    def hud_scene_key(self) -> str:
        return hud_scene_key_for(self.key)

    @property
    def is_system(self) -> bool:
        return self.layer not in (LAYER_LOCAL, LAYER_REMOTE)

    @property
    def is_remote(self) -> bool:
        return self.layer == LAYER_REMOTE

    def poi(self, key: str) -> MapPoi | None:
        return next((p for p in self.pois if p.key == key), None)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "navigation": {"mode": self.navigation_mode},
            "north_up": bool(self.north_up),
            "base_image": self.base_image,
            **({"base_image_sha256": self.base_image_sha256}
               if self.base_image_sha256 else {}),
            "ui": self.ui.to_dict(),
            "detectors": {"player_heading": self.heading.to_dict()},
            "pois": [p.to_dict() for p in self.pois],
        }

    @classmethod
    def from_dict(cls, d: dict, *, layer: str = "") -> MapDef:
        if not isinstance(d, dict):
            raise MapError(tr("地图定义必须是键值映射"))
        key = str(d.get("key") or "")
        name = str(d.get("name") or "")
        validate_map_key(key)
        if not name.strip():
            raise MapError(tr("地图名称不能为空"))
        navigation = d.get("navigation") or {}
        mode = str(navigation.get("mode") or NAV_MODE_CLOSED_LOOP)
        if mode not in NAV_MODES:
            raise MapError(tr("未知导航模式: {mode}").format(mode=mode))
        pois_raw = d.get("pois") or []
        if not isinstance(pois_raw, list):
            raise MapError(tr("pois 必须是列表"))
        pois = [MapPoi.from_dict(item) for item in pois_raw]
        seen: set[str] = set()
        for poi in pois:
            validate_poi_key(poi.key)
            if poi.key in seen:
                raise MapError(tr("POI key 重复: {key}").format(key=poi.key))
            seen.add(poi.key)
            if not (0.0 <= poi.x <= 1.0 and 0.0 <= poi.y <= 1.0):
                raise MapError(
                    tr("POI {key} 坐标超出底图范围").format(key=poi.key))
        base_image = str(d.get("base_image") or BASE_IMAGE_NAME)
        if base_image != BASE_IMAGE_NAME:
            raise MapError(tr("底图文件名固定为 base.png"))
        image_sha = str(d.get("base_image_sha256") or "").lower()
        if image_sha and not re.fullmatch(r"[0-9a-f]{64}", image_sha):
            raise MapError(tr("base_image_sha256 必须是 64 位十六进制摘要"))
        return cls(
            key=key,
            name=name,
            navigation_mode=mode,
            north_up=bool(d.get("north_up", True)),
            base_image=base_image,
            base_image_sha256=image_sha,
            ui=MapUiBinding.from_dict(d.get("ui") or {}, hud_scene_key_for(key)),
            pois=pois,
            heading=HeadingDetector.from_dict(
                (d.get("detectors") or {}).get("player_heading")),
            layer=layer,
        )


def validate_map_key(key: str) -> None:
    if not _KEY_RE.fullmatch(key or ""):
        raise MapError(tr("地图 key 必须以小写字母开头，仅含小写字母/数字/下划线"))


def validate_poi_key(key: str) -> None:
    if not _KEY_RE.fullmatch(key or ""):
        raise MapError(tr("POI key 必须以小写字母开头，仅含小写字母/数字/下划线"))


def hud_scene_key_for(map_key: str) -> str:
    return f"{HUD_SCENE_PREFIX}{map_key}"


def map_rel_path(map_key: str) -> str:
    return f"{MAPS_DIR}/{map_key}.yaml"


def base_image_rel_path(map_key: str, image_name: str = BASE_IMAGE_NAME) -> str:
    validate_map_key(map_key)
    if image_name != BASE_IMAGE_NAME:
        raise MapError(tr("底图文件名固定为 base.png"))
    return f"{MAPS_DIR}/{map_key}/{image_name}"


def image_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_map_yaml(map_def: MapDef) -> str:
    return yaml.safe_dump(
        map_def.to_dict(), allow_unicode=True, sort_keys=False,
        default_flow_style=False,
    )


class MapManager:
    """地图定义的读写、创建与删除。

    HUD 场景通过场景注册表创建，走和场景编辑器同一套 CRUD；``registry``
    可注入以便测试。
    """

    def __init__(self, resolver: ConfigResolver | None = None, registry=None):
        self._resolver = resolver or get_resolver()
        self._registry = registry

    @property
    def resolver(self) -> ConfigResolver:
        return self._resolver

    def _scene_registry(self):
        if self._registry is None:
            from .scene_registry import get_registry
            self._registry = get_registry()
        return self._registry

    # ─── 读取 ──────────────────────────────────────────────

    def list_keys(self) -> list[str]:
        return [
            name[:-len(".yaml")]
            for name in self._resolver.enumerate_entities(MAPS_DIR, "*.yaml")
        ]

    def list_maps(self) -> list[MapDef]:
        result: list[MapDef] = []
        for key in self.list_keys():
            try:
                result.append(self.load(key))
            except Exception as exc:  # noqa: BLE001 — 单个坏文件不拖垮列表
                logger.error(f"加载地图定义失败，已忽略 {key}: {exc}")
        return result

    def load(self, key: str) -> MapDef:
        rel = map_rel_path(key)
        path = self._resolver.resolve_read(rel)
        if path is None:
            raise MapError(tr("地图不存在: {key}").format(key=key))
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
        data.pop("content_version", None)
        origin = self._resolver.describe_entity(rel)
        map_def = MapDef.from_dict(data, layer=origin.layer)
        if map_def.key != key:
            raise MapError(
                tr("地图文件 {rel} 内的 key 为 {inner}，与文件名不一致")
                .format(rel=rel, inner=map_def.key))
        return map_def

    def exists(self, key: str) -> bool:
        return self._resolver.resolve_read(map_rel_path(key)) is not None

    def base_image_path(self, map_def: MapDef) -> Path | None:
        rel = base_image_rel_path(map_def.key, map_def.base_image)
        roots = {
            LAYER_LOCAL: self._resolver.local_dir,
            LAYER_REMOTE: self._resolver.remote_dir,
        }
        preferred = roots.get(map_def.layer, self._resolver.system_dir) / rel
        if map_def.layer == LAYER_REMOTE and not map_def.base_image_sha256:
            logger.error(
                f"远程地图缺少底图摘要，已拒绝加载底图: {map_def.key}")
            return None
        candidates = [preferred]
        # 本地影子可继续复用随包底图；remote 也只允许在摘要一致时退回随包图。
        system = self._resolver.system_dir / rel
        if system != preferred:
            candidates.append(system)
        for path in candidates:
            if not path.is_file():
                continue
            if map_def.base_image_sha256:
                try:
                    actual = image_sha256(path.read_bytes())
                except OSError as exc:
                    logger.warning(f"读取地图底图失败 {path}: {exc}")
                    continue
                if actual != map_def.base_image_sha256:
                    logger.error(
                        f"地图底图摘要不匹配，已拒绝加载: {map_def.key} "
                        f"expected={map_def.base_image_sha256} actual={actual}")
                    continue
            return path
        return None

    # ─── 写入 ──────────────────────────────────────────────

    def save(self, map_def: MapDef, *, force: bool = False) -> Path:
        """保存地图定义（开发模式写 system，用户模式写 local 影子）。"""
        MapDef.from_dict(map_def.to_dict())  # 复用同一套校验
        duplicate = next(
            (item for item in self.list_maps()
             if item.key != map_def.key
             and item.name.strip() == map_def.name.strip()),
            None,
        )
        if duplicate is not None:
            raise MapError(tr("地图名称已存在: {name}").format(name=map_def.name))
        self.validate_bindings(map_def)
        return self._resolver.write_entity(
            map_rel_path(map_def.key), dump_map_yaml(map_def), force=force)

    def import_base_image(self, map_def: MapDef, data: bytes) -> Path:
        """写入底图（PNG 字节）。底图不参与版本管理，直接落到写层。"""
        if not data:
            raise MapError(tr("底图数据为空"))
        map_def.base_image_sha256 = image_sha256(data)
        return self._resolver.write_entity(
            base_image_rel_path(map_def.key, map_def.base_image), data, force=True)

    def save_with_image(
        self, map_def: MapDef, data: bytes, *, force: bool = False,
    ) -> Path:
        """原子语义地保存底图与定义；定义失败时恢复写层原底图。"""
        rel = base_image_rel_path(map_def.key, map_def.base_image)
        root = (self._resolver.system_dir if self._resolver.is_dev_mode()
                else self._resolver.local_dir)
        target = root / rel
        existed = target.is_file()
        previous = target.read_bytes() if existed else b""
        previous_sha = map_def.base_image_sha256
        self.import_base_image(map_def, data)
        try:
            return self.save(map_def, force=force)
        except Exception:
            map_def.base_image_sha256 = previous_sha
            if existed:
                self._resolver.write_entity(rel, previous, force=True)
            elif target.exists():
                target.unlink()
            raise

    def copy_to_local(self, key: str) -> MapDef:
        """把 system/remote 地图复制为 local 影子（含底图），供用户修改。"""
        map_def = self.load(key)
        image = self.base_image_path(map_def)
        writer = self
        if self._resolver.is_dev_mode():
            # 开发模式普通 write_entity 固定写 system；“复制到本地”必须显式
            # 使用用户模式 resolver，否则会把远程内容误写进随包 system 层。
            local_resolver = ConfigResolver(
                system_dir=self._resolver.system_dir,
                local_dir=self._resolver.local_dir,
                remote_dir=self._resolver.remote_dir,
                dev_mode=False,
            )
            writer = MapManager(local_resolver, registry=self._scene_registry())
        if image is not None:
            writer.save_with_image(map_def, image.read_bytes(), force=True)
        else:
            writer.save(map_def, force=True)
        return self.load(key)

    def create(self, key: str, name: str,
               navigation_mode: str = NAV_MODE_CLOSED_LOOP) -> MapDef:
        """新建地图：写定义文件并生成专属 HUD 场景。"""
        validate_map_key(key)
        if not name.strip():
            raise MapError(tr("地图名称不能为空"))
        if navigation_mode not in NAV_MODES:
            raise MapError(tr("未知导航模式: {mode}").format(mode=navigation_mode))
        if self.exists(key):
            raise MapError(tr("地图 key 已存在: {key}").format(key=key))
        if any(item.name.strip() == name.strip() for item in self.list_maps()):
            raise MapError(tr("地图名称已存在: {name}").format(name=name.strip()))
        scene_key = hud_scene_key_for(key)
        scene_preexisting = self._scene_registry().get_scene(scene_key) is not None
        created_scene = False
        try:
            created_scene = self._ensure_hud_scene(scene_key, name)
            map_def = MapDef(
                key=key, name=name, navigation_mode=navigation_mode,
                ui=MapUiBinding(scene=scene_key, generated_scene=created_scene),
            )
            self.save(map_def)
        except Exception:
            if (created_scene or not scene_preexisting) \
                    and self._scene_registry().get_scene(scene_key) is not None:
                self._delete_hud_scene(scene_key)
            raise
        logger.info(f"已创建地图: {key} ({name})，HUD 场景 {scene_key}")
        return self.load(key)

    def delete(self, key: str, *, delete_hud_scene: bool = True) -> None:
        """删除地图定义与底图；自动生成的 HUD 场景默认一并删除。

        用户模式下 system 地图不可删（``SystemContentProtected``），与其它
        实体一致。
        """
        map_def = self.load(key)
        self._resolver.ensure_entity_deletable(map_rel_path(key))
        self._resolver.delete_entity(map_rel_path(key))
        image_rel = base_image_rel_path(key, map_def.base_image)
        if self._resolver.resolve_read(image_rel) is not None:
            try:
                self._resolver.delete_entity(image_rel)
            except Exception as exc:  # noqa: BLE001 — 底图删不掉不阻断定义删除
                logger.warning(f"删除底图失败 {image_rel}: {exc}")
        if (delete_hud_scene and map_def.ui.generated_scene
                and map_def.ui.scene == map_def.hud_scene_key):
            shared_by = [
                item.key for item in self.list_maps()
                if item.key != key and item.ui.scene == map_def.ui.scene
            ]
            if shared_by:
                logger.warning(
                    f"HUD 场景 {map_def.ui.scene} 仍被地图引用，已保留: "
                    f"{', '.join(shared_by)}")
            else:
                self._delete_hud_scene(map_def.hud_scene_key)
        logger.info(f"已删除地图: {key}")

    # ─── HUD 场景 ──────────────────────────────────────────

    def _ensure_hud_scene(self, scene_key: str, map_name: str) -> bool:
        registry = self._scene_registry()
        if registry.get_scene(scene_key) is not None:
            return False
        if HUD_SCENE_GROUP_KEY not in dict(registry.get_groups()):
            registry.create_group(HUD_SCENE_GROUP_KEY, HUD_SCENE_GROUP_NAME)
            # 场景文件写入会同步触发注册表按 scenes.yaml 热重载，内存里新建
            # 的分组会被冲掉；先把分组落盘再建场景。
            registry.save_group_config()
        registry.create_scene(
            scene_key, tr("{name}地图").format(name=map_name),
            group_key=HUD_SCENE_GROUP_KEY)
        # 同理：归属关系也要在下一次场景写入触发热重载前落盘，否则新场景会
        # 被重载临时归到第一个分组。
        registry.save_group_config()
        registry.add_scene_view(scene_key, FULL_VIEW_KEY, tr("大地图"))
        for view, kind, entity_key, entity_name, clickable in HUD_SCENE_ENTITIES:
            views = [view] if view else []
            if kind == "region":
                registry.add_region_to_scene(scene_key, RegionDef(
                    key=entity_key, name=entity_name, type="func",
                    is_text=False, is_clickable=clickable, views=views,
                ))
            else:
                registry.add_point_to_scene(scene_key, PointDef(
                    key=entity_key, name=entity_name, type="func",
                    is_text=False, is_clickable=clickable, views=views,
                ))
        return True

    def validate_bindings(self, map_def: MapDef) -> None:
        """校验 HUD 引用；测试桩未提供 registry API 时只做结构校验。"""
        registry = self._scene_registry()
        if not hasattr(registry, "get_scene"):
            return
        scene = registry.get_scene(map_def.ui.scene)
        if scene is None:
            raise MapError(tr("HUD 场景不存在: {scene}").format(
                scene=map_def.ui.scene))
        regions = {item.key: item for item in scene.regions}
        points = {item.key: item for item in scene.points}
        for attr in ("minimap", "open_map", "full_map", "close_map"):
            key = getattr(map_def.ui, attr)
            if key not in regions:
                raise MapError(tr("HUD 绑定 {attr} 必须引用区域，未找到: {key}").format(
                    attr=attr, key=key))
        if map_def.ui.minimap_center not in points:
            raise MapError(tr("HUD 绑定 minimap_center 必须引用坐标点，未找到: {key}").format(
                key=map_def.ui.minimap_center))
        for attr in ("minimap", "open_map"):
            item = regions[getattr(map_def.ui, attr)]
            if item.views:
                raise MapError(tr("HUD 绑定 {attr} 必须属于基础视图").format(attr=attr))
        center = points[map_def.ui.minimap_center]
        if center.views:
            raise MapError(tr("HUD 绑定 minimap_center 必须属于基础视图"))
        for attr in ("full_map", "close_map"):
            item = regions[getattr(map_def.ui, attr)]
            if FULL_VIEW_KEY not in item.views:
                raise MapError(tr("HUD 绑定 {attr} 必须属于 full 视图").format(attr=attr))

    def _delete_hud_scene(self, scene_key: str) -> None:
        registry = self._scene_registry()
        if registry.get_scene(scene_key) is None:
            return
        try:
            registry.delete_scene(scene_key)
            registry.save_group_config()
        except Exception as exc:  # noqa: BLE001 — 被引用或受保护时保留场景
            logger.warning(f"HUD 场景 {scene_key} 未删除: {exc}")
