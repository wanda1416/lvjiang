"""工作流与布局共用的合法按键校验。"""

from __future__ import annotations

from collections import defaultdict

from .key_names import KNOWN_KEY_NAMES, normalize_key
from .layout_models import Layout

# 对外提供只读合法键名库；别名仍由 normalize_key 统一收敛。
VALID_KEY_NAMES = KNOWN_KEY_NAMES


def validate_key_name(name: str) -> str:
    """校验并返回标准键名；非法键名抛出 ``ValueError``。"""
    return normalize_key(name)


def validate_layout_activation_keys(
    layout: Layout,
    scene_keys: set[str] | None = None,
) -> None:
    """校验布局中区域和坐标点绑定的所有按键。

    ``scene_keys`` 为 ``None`` 时校验整个布局。发现问题时一次列出
    全部非法或同视图重复绑定，供 UI 与非 UI 保存入口共用。
    """
    problems: list[str] = []
    bindings: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    # 延迟导入避免 key_validation 与 layout_manager/scene_registry
    # 在模块初始化期形成循环依赖。
    from .scene_definition_models import BASE_VIEW_KEY
    from .scene_registry import get_registry

    registry = get_registry()
    mappings = (("区域", layout.regions), ("坐标", layout.points))
    for kind, scenes in mappings:
        for scene_key, items in scenes.items():
            if scene_keys is not None and scene_key not in scene_keys:
                continue
            scene = registry.get_scene(scene_key)
            definitions = (
                scene.regions if kind == "区域" else scene.points
            ) if scene else []
            definition_by_key = {item.key: item for item in definitions}
            for item in items:
                key_name = getattr(item, "activation_key", "")
                if not key_name:
                    continue
                if not isinstance(key_name, str):
                    problems.append(
                        f"[{scene_key}].[{item.key}] {kind}绑定 {key_name!r}"
                    )
                    continue
                try:
                    normalized = validate_key_name(key_name)
                except ValueError:
                    problems.append(
                        f"[{scene_key}].[{item.key}] {kind}绑定 {key_name!r}"
                    )
                    continue
                definition = definition_by_key.get(item.key)
                view = (getattr(definition, "view", "") or BASE_VIEW_KEY)
                bindings[(scene_key, view, normalized)].append(
                    f"{kind} [{item.key}]"
                )
    for (scene_key, view, key_name), targets in bindings.items():
        if len(targets) > 1:
            problems.append(
                f"[{scene_key}] 视图 [{view}] 按键 {key_name} 重复绑定 "
                + "、".join(targets)
            )
    if problems:
        raise ValueError("布局按键绑定无效：" + "、".join(problems))
