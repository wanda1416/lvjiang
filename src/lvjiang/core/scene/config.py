"""场景目录化配置的加载、合并视图与保存（schema v3）。

``scenes.yaml`` 聚合注册表已退役。每个配置层（namespace）的 ``scenes/`` 目录
自洽成立：目录即归属，``_meta.json`` 只管顺序::

    scenes/_meta.json          # {"groups": [{"key": "general", "scenes": [...]}]}
    scenes/{group}.yaml        # 分组定义：name（唯一的分组元数据）
    scenes/{group}/{scene}.yaml  # 场景定义；目录决定分组归属

不变量（加载器与写入路径共同保证）：

- ``scenes/*.yaml``（顶层）**只有**分组定义；场景文件必然位于 ``scenes/{group}/``
  之下。顶层 yaml 不在分组清单里时按 warning 忽略，不当场景加载。
- 分组定义文件与同名分组子目录并存（``scenes/map.yaml`` + ``scenes/map/``）
  是合法且唯一的形态。
- 分组 key 与场景 key 分属两个命名空间，允许同名不冲突。

合并语义（namespace 优先级链 ``system > local > 插件``）：

- 分组列表 = system 区间 + local 区间；区间内部按 ``_meta.json`` 顺序。
  local 的 ``_meta.json`` 同时充当**排序补丁**：它列出的 system 分组/场景 key
  会重排 system 区间内部的顺序，未列出的按 system 自己的 meta 顺序追加在后。
  区间之间永不交错——system 恒在 local 之前，写入侧同样钳制。
- 分组名：system ``{group}.yaml`` 为基底，local 同名文件可覆盖 ``name``。
- 场景定义：一物一文件整影子（local 同路径顶掉 system），与其它实体一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from loguru import logger

from ..config.resolver import ConfigResolver, get_resolver

SCENE_META_REL = "scenes/_meta.json"
#: 排序/加载兜底分组
FALLBACK_GROUP = "general"


@dataclass(frozen=True)
class SceneManifest:
    """供 ``SceneRegistry`` 消费的稳定运行时视图。"""

    order: list[str]
    groups: dict[str, list[str]]
    group_names: dict[str, str]
    disabled: set[str] = field(default_factory=set)
    #: 分组/场景的归属层（system | local），供排序钳制与来源展示
    group_layers: dict[str, str] = field(default_factory=dict)
    scene_layers: dict[str, str] = field(default_factory=dict)


# ─── namespace 链 ────────────────────────────────────────

def scene_namespaces(resolver: ConfigResolver) -> list[tuple[str, "object"]]:
    """namespace 优先级链：system > local（> 插件，经注册扩展）。

    返回 [(name, root_path), ...]；system 与 local 指向同一目录（测试单层
    布局）时只保留一份。
    """
    roots: list[tuple[str, object]] = [
        ("system", resolver.system_dir), ("local", resolver.local_dir)]
    deduped: list[tuple[str, object]] = []
    seen: set = set()
    for name, root in roots:
        if root in seen:
            continue
        seen.add(root)
        deduped.append((name, root))
    return deduped


def register_scene_namespace(name: str, root) -> None:
    """插件声明自己的场景 namespace，追加到链尾（当前占位：消费方暂固定两层）。"""
    logger.debug(f"场景 namespace 注册（暂不参与读取）: {name} -> {root}")


# ─── 读取 ────────────────────────────────────────────────

def _read_meta(root) -> dict[str, list[str]]:
    """读一层 ``scenes/_meta.json`` → {group_key: [scene_key, ...]}。

    缺失/损坏返回空 dict（该层降级为目录名序 + 文件名序），不阻断加载。
    """
    path = root / SCENE_META_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        groups = data.get("groups")
        if not isinstance(groups, list):
            raise ValueError("groups 必须是列表")
        result: dict[str, list[str]] = {}
        for item in groups:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                raise ValueError("groups 条目必须是 {key, scenes}")
            scenes = item.get("scenes") or []
            if not isinstance(scenes, list) \
                    or not all(isinstance(s, str) for s in scenes):
                raise ValueError(f"分组 {item['key']} 的 scenes 必须是字符串列表")
            result[item["key"]] = list(scenes)
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"scenes/_meta.json 解析失败，该层顺序降级为目录序: {path}: {e}")
        return {}


def _group_yaml(root, group_key: str):
    return root / "scenes" / f"{group_key}.yaml"


def _group_dir(root, group_key: str):
    return root / "scenes" / group_key


def _read_group_name(root, group_key: str) -> str | None:
    path = _group_yaml(root, group_key)
    if not path.is_file():
        return None
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"分组定义解析失败 {path}: {e}")
        return None
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name
    return None


def _layer_groups(root) -> tuple[list[str], dict[str, list[str]]]:
    """一层的分组清单与成员：``_meta.json`` ∪ ``scenes/*.yaml`` ∪ ``scenes/*/``。

    目录是归属与存在的唯一事实源，meta 只管顺序；三方取并集，顺序按 meta
    在前、未列条目稳定追加在后。
    """
    meta = _read_meta(root)
    base = root / "scenes"
    if not base.is_dir():
        return [], {}
    found: list[str] = list(meta)
    for path in sorted(base.iterdir()):
        if path.name.startswith(("_", ".")):
            continue
        if path.is_dir():
            key = path.name
        elif path.is_file() and path.suffix == ".yaml":
            # 顶层 yaml 只允许是分组定义；key 取 stem（scenes/{group}.yaml）
            key = path.stem
        else:
            continue
        if key not in found:
            found.append(key)
    ordered: list[str] = []
    for key in found:
        if key not in ordered:
            ordered.append(key)
    members: dict[str, list[str]] = {}
    for group_key in ordered:
        group_dir = _group_dir(root, group_key)
        scene_keys = []
        if group_dir.is_dir():
            scene_keys = sorted(
                p.stem for p in group_dir.glob("*.yaml") if p.is_file())
        meta_scenes = meta.get(group_key) or []
        merged = [s for s in meta_scenes if s in scene_keys]
        merged.extend(s for s in scene_keys if s not in merged)
        members[group_key] = merged
    return ordered, members


def _apply_order_patch(keys: list[str], owner_keys: list[str],
                       patch: list[str]) -> list[str]:
    """排序补丁：patch 中属于本区块的 key 按补丁顺序在前，其余按原顺序在后。

    区块归属由 ``owner_keys``（该区块自己的 key 全集）决定；patch 里不属于
    本区块的 key 忽略——跨区块重排在加载侧永远无效，保证 system 恒在 local 前。
    """
    mine = set(owner_keys)
    patched = [k for k in patch if k in mine]
    patched.extend(k for k in keys if k not in patched)
    return patched


def load_scene_manifest(resolver: ConfigResolver | None = None) -> SceneManifest:
    """构建合并视图：system 区间在前、local 区间在后，区间内按 meta 顺序。"""
    resolver = resolver or get_resolver()
    namespaces = scene_namespaces(resolver)
    local_meta: dict[str, list[str]] = {}
    if len(namespaces) > 1:
        local_meta = _read_meta(namespaces[-1][1])
    patch_group_order: list[str] = list(local_meta)
    patch_scene_order: dict[str, list[str]] = {
        key: list(scenes) for key, scenes in local_meta.items()}

    groups: dict[str, list[str]] = {}
    group_names: dict[str, str] = {}
    group_layers: dict[str, str] = {}
    scene_layers: dict[str, str] = {}
    merged_group_order: list[str] = []

    for layer, root in namespaces:
        layer_group_order, layer_members = _layer_groups(root)
        # 分组顺序：local meta 作为排序补丁在本区块内生效
        ordered = _apply_order_patch(
            layer_group_order, layer_group_order, patch_group_order)
        for group_key in ordered:
            if group_key in groups:
                continue
            groups[group_key] = []
            group_layers[group_key] = layer
            name = _read_group_name(root, group_key)
            if name is None and layer == "system":
                # system 分组缺名时尝试 local 覆盖；再缺则退回 key
                pass
            group_names[group_key] = name or group_key
            merged_group_order.append(group_key)
        # 场景成员：system 区间的场景在前，local 追加在后
        for group_key, members in layer_members.items():
            if group_key not in groups:
                # 分组目录存在但没有分组定义（isolated）→ 兜底收录
                groups[group_key] = []
                group_layers[group_key] = layer
                group_names[group_key] = group_key
                merged_group_order.append(group_key)
            ordered_scenes = _apply_order_patch(
                members, members, patch_scene_order.get(group_key, []))
            for scene_key in ordered_scenes:
                if scene_key in scene_layers:
                    continue
                groups[group_key].append(scene_key)
                scene_layers[scene_key] = layer

    # 分组名：system 基底 ← local 覆盖（local {group}.yaml 只写 name 时生效）
    for layer, root in namespaces:
        if layer == "system":
            continue
        for group_key in list(groups):
            override = _read_group_name(root, group_key)
            if override is not None:
                group_names[group_key] = override

    order = [s for g in merged_group_order for s in groups.get(g, [])]
    return SceneManifest(
        order=order,
        groups=groups,
        group_names=group_names,
        disabled=set(),
        group_layers=group_layers,
        scene_layers=scene_layers,
    )


# ─── 保存 ────────────────────────────────────────────────

def build_scene_meta(
    group_order: list[str],
    groups: dict[str, list[str]],
) -> dict:
    """``_meta.json`` 的唯一持久化形式：分组顺序 + 各组场景顺序。"""
    return {
        "groups": [
            {"key": key, "scenes": list(groups.get(key, []))}
            for key in group_order
        ],
    }


def save_scene_meta(
    resolver: ConfigResolver,
    group_order: list[str],
    groups: dict[str, list[str]],
) -> None:
    """写 ``scenes/_meta.json``：开发模式写 system；用户模式写 local 排序补丁。"""
    doc = build_scene_meta(group_order, groups)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    resolver.write_entity(SCENE_META_REL, text)


def save_group_name(resolver: ConfigResolver, group_key: str, name: str) -> None:
    """写分组定义 ``scenes/{group}.yaml``（开发 → system；用户 → local 覆盖名）。"""
    import yaml
    rel = f"scenes/{group_key}.yaml"
    resolver.write_entity(rel, yaml.safe_dump(
        {"name": name}, allow_unicode=True, default_flow_style=False,
        sort_keys=False))


def ensure_group_dir(resolver: ConfigResolver, group_key: str) -> None:
    """确保分组子目录存在（新建分组时；dev 与 user 都落在各自可写层）。"""
    root = resolver.system_dir if resolver.is_dev_mode() else resolver.local_dir
    _group_dir(root, group_key).mkdir(parents=True, exist_ok=True)


def scene_rel(group_key: str, scene_key: str) -> str:
    """场景定义文件路径：分组即目录。"""
    return f"scenes/{group_key}/{scene_key}.yaml"
