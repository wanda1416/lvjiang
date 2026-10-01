"""工作流与布局共用的合法按键校验。"""

from __future__ import annotations

from .key_names import KNOWN_PRESS_NAMES, normalize_pressable
from .layout_models import Layout

# 对外提供只读合法键名库；别名仍由 normalize_key 统一收敛。
VALID_KEY_NAMES = KNOWN_PRESS_NAMES


def validate_key_name(name: str) -> str:
    """校验并返回标准 press 名；非法键名抛出 ``ValueError``。"""
    return normalize_pressable(name)


def validate_layout_activation_keys(
    layout: Layout,
    scene_keys: set[str] | None = None,
) -> None:
    """校验布局中区域和坐标点绑定的所有按键是否是合法键名。

    ``scene_keys`` 为 ``None`` 时校验整个布局，发现问题时一次列出全部非法绑定，
    供 UI 与非 UI 保存入口共用。

    **不禁止同一视图内重复绑定**：绑定方向是「区域 → 按键」，一个区域用哪个键
    激活与别的区域无关，同一个键服务多个区域是真实存在的——暗杀和拾取都用 F，
    它们是同一画面上两个不同的区域（各有自己的 OCR 范围），只是共用触发键。
    反过来「按键 → 区域」才需要唯一，而布局里没有这个方向的查询。
    """
    problems: list[str] = []
    mappings = (("区域", layout.regions), ("坐标", layout.points))
    for kind, scenes in mappings:
        for scene_key, items in scenes.items():
            if scene_keys is not None and scene_key not in scene_keys:
                continue
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
                    validate_key_name(key_name)
                except ValueError:
                    problems.append(
                        f"[{scene_key}].[{item.key}] {kind}绑定 {key_name!r}"
                    )
                    continue
    if problems:
        raise ValueError("布局按键绑定无效：" + "、".join(problems))
