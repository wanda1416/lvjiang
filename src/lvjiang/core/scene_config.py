"""``scenes.yaml`` 的结构版本、加载与序列化。

仅支持 v2；与
``layouts.yaml`` 一样由一个领域顶层键承载全部内容：

.. code-block:: yaml

    schema_version: 2
    scenes:
      general:
        name: 通用
        items:
          - game_main_page
      activity:
        name: 活动
        items:
          - activity_main
          - activity_mengzhu
        disabled:
          - activity_mengzhu

``disabled`` 只控制场景管理界面的展示；场景定义仍会加载，因此旧工作流和
布局对它的引用不会失效。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import yaml

from .config.resolver import LAYER_LOCAL, ConfigResolver, merge_doc

SCENES_SCHEMA_VERSION = 2
SCENES_REGISTRY_PATHS = ("scenes.*.items", "scenes.*.disabled")


@dataclass(frozen=True)
class SceneManifest:
    """供 ``SceneRegistry`` 消费的稳定运行时视图。"""

    order: list[str]
    groups: dict[str, list[str]]
    group_names: dict[str, str]
    disabled: set[str]


def _version_of(doc: dict) -> int:
    value = doc.get("schema_version")
    if value is None:
        # 空覆盖、仅元数据与当前 scenes 增量均可省略版本。
        return SCENES_SCHEMA_VERSION
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("scenes.yaml 的 schema_version 必须是整数")
    return value


def normalize_scene_doc(doc: dict) -> dict:
    """验证一层当前场景配置，不转换旧结构，也不写文件。"""
    if not isinstance(doc, dict):
        return {}
    version = _version_of(doc)
    if version > SCENES_SCHEMA_VERSION:
        raise ValueError(
            f"scenes.yaml schema_version={version}，当前只支持到 "
            f"{SCENES_SCHEMA_VERSION}")
    if version != SCENES_SCHEMA_VERSION or {"layout_scenes", "group_names"} & doc.keys():
        raise ValueError(
            "scenes.yaml 旧 v1 结构不再兼容；请使用 schema_version: 2 和 "
            "scenes.<分组>.name/items 格式，保留原有列表增量")
    result = deepcopy(doc)
    v2_groups = result.get("scenes")
    if v2_groups is not None and not isinstance(v2_groups, dict):
        raise ValueError("scenes.yaml 的 scenes 必须是映射")
    return result


def load_scene_doc(resolver: ConfigResolver) -> dict:
    """分别验证 system/local 后再合并，禁止跨 schema 直接深合并。"""
    system = normalize_scene_doc(resolver.load_system("scenes.yaml"))
    local = normalize_scene_doc(resolver.load_local("scenes.yaml"))
    return merge_doc(system, local, SCENES_REGISTRY_PATHS)


def parse_scene_manifest(doc: dict) -> SceneManifest:
    """校验 v2 合并视图并转为注册表需要的结构。"""
    normalized = normalize_scene_doc(doc)
    raw_groups = normalized.get("scenes") or {}
    groups: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    disabled: set[str] = set()
    order: list[str] = []

    for group_key, raw_group in raw_groups.items():
        if not isinstance(group_key, str) or not isinstance(raw_group, dict):
            raise ValueError("scenes.yaml 的分组 key 和定义格式无效")
        name = raw_group.get("name", group_key)
        items = raw_group.get("items", [])
        hidden = raw_group.get("disabled", [])
        if not isinstance(name, str) or not isinstance(items, list):
            raise ValueError(f"场景分组 {group_key} 的 name/items 格式无效")
        if not isinstance(hidden, list):
            raise ValueError(f"场景分组 {group_key} 的 disabled 必须是列表")
        if not all(isinstance(key, str) for key in items + hidden):
            raise ValueError(f"场景分组 {group_key} 只能包含字符串场景 key")
        unknown = [key for key in hidden if key not in items]
        if unknown:
            raise ValueError(
                f"场景分组 {group_key} 的 disabled 含未登记场景: {unknown}")
        duplicates = [key for key in items if items.count(key) > 1]
        if duplicates:
            raise ValueError(f"场景分组 {group_key} 存在重复场景: {sorted(set(duplicates))}")
        groups[group_key] = list(items)
        names[group_key] = name
        disabled.update(hidden)
        order.extend(items)

    if len(order) != len(set(order)):
        raise ValueError("同一场景不能登记在多个场景分组中")
    return SceneManifest(order, groups, names, disabled)


def load_scene_manifest(resolver: ConfigResolver) -> SceneManifest:
    return parse_scene_manifest(load_scene_doc(resolver))


def build_scene_doc(
    group_order: list[str],
    groups: dict[str, list[str]],
    group_names: dict[str, str],
    disabled: set[str],
) -> dict:
    """从注册表状态构建唯一的 v2 持久化形式。"""
    result: dict = {"schema_version": 2, "scenes": {}}
    for group_key in group_order:
        items = list(groups.get(group_key, []))
        group = {
            "name": group_names.get(group_key, group_key),
            "items": items,
        }
        hidden = [key for key in items if key in disabled]
        if hidden:
            group["disabled"] = hidden
        result["scenes"][group_key] = group
    return result


def save_scene_doc(resolver: ConfigResolver, doc: dict) -> None:
    """保存 v2；用户模式相对 system v2 计算增量。"""
    normalized = normalize_scene_doc(doc)
    local = normalize_scene_doc(resolver.load_local("scenes.yaml"))
    system = normalize_scene_doc(resolver.load_system("scenes.yaml"))
    if resolver.is_dev_mode():
        local_only = set(local.get("scenes", {})) - set(system.get("scenes", {}))
        if local_only:
            system_doc = deepcopy(normalized)
            local_doc = deepcopy(local)
            for key in local_only:
                group = normalized.get("scenes", {}).get(key)
                system_doc.get("scenes", {}).pop(key, None)
                if group is None:
                    local_doc.get("scenes", {}).pop(key, None)
                else:
                    local_doc.setdefault("scenes", {})[key] = deepcopy(group)
            resolver.save_merged("scenes.yaml", system_doc,
                                 registry=SCENES_REGISTRY_PATHS)
            if local_doc.get("scenes"):
                resolver.write_entity(
                    "scenes.yaml",
                    yaml.dump(local_doc, allow_unicode=True,
                              default_flow_style=False, sort_keys=False),
                    layer=LAYER_LOCAL)
            else:
                resolver.delete_entity("scenes.yaml", layer=LAYER_LOCAL)
            return
    resolver.save_merged("scenes.yaml", normalized, base_doc=system,
                         registry=SCENES_REGISTRY_PATHS)
