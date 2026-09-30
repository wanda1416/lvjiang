"""``layouts.yaml`` v2 manifest parsing and persistence.

Layout keys are stable storage identities.  Display names are metadata and must
never be used to construct paths or persisted references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config.resolver import ConfigResolver, get_resolver

LAYOUTS_SCHEMA_VERSION = 2
LAYOUT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: 画布宽高比的默认容差（相对误差）。取 0.5%：1920 宽上约 10px，足以吸收
#: 边框像素与画布比例换算的舍入差，又拦得住 16:9 与 20:9 这种真正的错配。
DEFAULT_ASPECT_TOLERANCE = 0.005
#: 容差上限。放到 10% 以上就等于没有校验，只会给人「配了却不生效」的错觉。
MAX_ASPECT_TOLERANCE = 0.1
#: 合理的宽高比区间。越界基本只有一种来源：YAML 把没加引号的 20:9 当六十进制
#: 读成了 1209，与其让它静默生效成一条永远不满足的要求，不如加载即报错。
MIN_ASPECT_VALUE = 0.1
MAX_ASPECT_VALUE = 10.0

_ASPECT_PAIR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[:：/]\s*(\d+(?:\.\d+)?)\s*$")


def parse_aspect_ratio(text: object) -> float | None:
    """画布宽高比声明 → 比值；空值返回 None（表示不校验）。

    两种写法都接受，因为两种都符合直觉：``20:9`` 说的是屏幕比例本身，
    ``2.22`` 是算好的比值。冒号写法按原值相除，不做取整——``2560:1440``
    与 ``16:9`` 必须算出同一个数，否则同一块屏幕会因为写法不同而一个过一个不过。
    """
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError("画布宽高比不能是布尔值")
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        raw = str(text).strip()
        if not raw:
            return None
        matched = _ASPECT_PAIR.match(raw)
        if matched is not None:
            width, height = float(matched.group(1)), float(matched.group(2))
            if height <= 0 or width <= 0:
                raise ValueError(f"画布宽高比的两边必须为正数: {raw}")
            return width / height
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(
                f"画布宽高比格式无法识别: {raw!r}；写成 20:9 或 2.22"
            ) from None
    if value <= 0:
        raise ValueError("画布宽高比必须为正数")
    if not MIN_ASPECT_VALUE <= value <= MAX_ASPECT_VALUE:
        # YAML 1.1 把不加引号的 20:9 当六十进制读成 1209，正好落在这里。
        # 真实屏幕比例都在 0.1~10 之间，越界一律当写错处理并点出原因。
        raise ValueError(
            f"画布宽高比 {value:g} 超出合理范围 "
            f"[{MIN_ASPECT_VALUE}, {MAX_ASPECT_VALUE}]；"
            "在 YAML 里写 20:9 这种形式要加引号，否则会被当成六十进制数字"
        )
    return value


def canvas_aspect_deviation(
    expected_ratio: float,
    canvas_width_px: float,
    canvas_height_px: float,
) -> float | None:
    """画布实际宽高比相对声明值的偏差（相对误差）；尺寸非法时返回 None。

    比的是**画布裁剪之后**的尺寸，不是整张截图——窗口边框和标题栏本就不该参与，
    否则同一台机器上窗口模式与全屏会算出两个不同的比例。
    """
    if canvas_width_px <= 0 or canvas_height_px <= 0 or expected_ratio <= 0:
        return None
    actual = canvas_width_px / canvas_height_px
    return abs(actual - expected_ratio) / expected_ratio


@dataclass(frozen=True)
class LayoutEntry:
    key: str
    name: str
    desc: str
    canvas: dict
    extends: str = ""
    #: 画布裁剪后应有的宽高比，形如 ``20:9`` / ``2.22``；空串表示不校验
    aspect: str = ""
    aspect_tolerance: float = DEFAULT_ASPECT_TOLERANCE

    @property
    def aspect_ratio(self) -> float | None:
        """声明的比值；未声明返回 None。清单加载时已校验过格式。"""
        return parse_aspect_ratio(self.aspect)


def validate_layout_key(key: str) -> str:
    value = str(key).strip()
    if LAYOUT_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "布局 key 必须以小写字母开头，且只能包含小写字母、数字和下划线"
        )
    return value


def parse_layout_entries(doc: dict) -> dict[str, LayoutEntry]:
    if not isinstance(doc, dict):
        raise ValueError("layouts.yaml 根节点必须是映射")
    version = doc.get("schema_version")
    if version != LAYOUTS_SCHEMA_VERSION or isinstance(version, bool):
        raise ValueError(
            "layouts.yaml 仅支持 schema_version: 2，旧布局配置不再兼容"
        )
    raw_layouts = doc.get("layouts")
    if not isinstance(raw_layouts, dict):
        raise ValueError("layouts.yaml 的 layouts 必须是映射")

    entries: dict[str, LayoutEntry] = {}
    names: set[str] = set()
    for raw_key, raw_entry in raw_layouts.items():
        if not isinstance(raw_key, str):
            raise ValueError("布局 key 必须是字符串")
        key = validate_layout_key(raw_key)
        if key != raw_key:
            raise ValueError(f"布局 key 不能包含首尾空白: {raw_key!r}")
        if not isinstance(raw_entry, dict):
            raise ValueError(f"布局 {key} 的定义必须是映射")
        name = raw_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"布局 {key} 必须声明非空 name")
        if name != name.strip():
            raise ValueError(f"布局 {key} 的 name 不能包含首尾空白")
        if name in names:
            raise ValueError(f"布局名称重复: {name}")
        names.add(name)
        desc = raw_entry.get("desc", "")
        if not isinstance(desc, str):
            raise ValueError(f"布局 {key} 的 desc 必须是字符串")
        canvas = raw_entry.get("canvas", {})
        if not isinstance(canvas, dict):
            raise ValueError(f"布局 {key} 的 canvas 必须是映射")
        aspect_raw = raw_entry.get("aspect", "")
        if isinstance(aspect_raw, bool):
            raise ValueError(f"布局 {key} 的 aspect 不能是布尔值")
        aspect = ("" if aspect_raw is None
                  else str(aspect_raw).strip() if isinstance(aspect_raw, str)
                  else str(aspect_raw))
        try:
            parse_aspect_ratio(aspect)
        except ValueError as exc:
            raise ValueError(f"布局 {key} 的 aspect 无效: {exc}") from None
        tolerance_raw = raw_entry.get(
            "aspect_tolerance", DEFAULT_ASPECT_TOLERANCE)
        if isinstance(tolerance_raw, bool) or not isinstance(
                tolerance_raw, (int, float)):
            raise ValueError(f"布局 {key} 的 aspect_tolerance 必须是数字")
        tolerance = float(tolerance_raw)
        if not 0 < tolerance <= MAX_ASPECT_TOLERANCE:
            raise ValueError(
                f"布局 {key} 的 aspect_tolerance 必须在 0 与 "
                f"{MAX_ASPECT_TOLERANCE} 之间")
        extends = raw_entry.get("extends", "")
        if not isinstance(extends, str):
            raise ValueError(f"布局 {key} 的 extends 必须是字符串")
        if extends:
            normalized_parent = validate_layout_key(extends)
            if normalized_parent != extends:
                raise ValueError(
                    f"布局 {key} 的 extends 不能包含首尾空白")
        entries[key] = LayoutEntry(
            key, name, desc, canvas, extends, aspect, tolerance)

    for entry in entries.values():
        if not entry.extends:
            continue
        if entry.extends == entry.key:
            raise ValueError(f"布局 {entry.key} 不能继承自身")
        parent = entries.get(entry.extends)
        if parent is None:
            raise ValueError(
                f"布局 {entry.key} 的 extends 目标不存在: {entry.extends}"
            )
        if parent.extends:
            raise ValueError(
                f"布局 {entry.key} 的 extends 只能指向根布局，禁止多级继承"
            )
    return entries


def load_layout_doc(resolver: ConfigResolver | None = None) -> dict:
    resolver = resolver or get_resolver()
    doc = resolver.load_merged("layouts.yaml")
    if not doc:
        return {"schema_version": LAYOUTS_SCHEMA_VERSION, "layouts": {}}
    parse_layout_entries(doc)
    return doc


def load_layout_entries(
    resolver: ConfigResolver | None = None,
) -> dict[str, LayoutEntry]:
    return parse_layout_entries(load_layout_doc(resolver))


def save_layout_doc(doc: dict, resolver: ConfigResolver | None = None) -> None:
    resolver = resolver or get_resolver()
    parse_layout_entries(doc)
    resolver.save_merged("layouts.yaml", doc)
