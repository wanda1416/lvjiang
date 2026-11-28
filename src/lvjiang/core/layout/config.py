"""布局目录化清单（一布局一文件 + ``_meta.json`` 顺序）的解析与持久化。

``layouts.yaml`` 聚合注册表已退役。每个配置层的 ``layouts/`` 目录自洽成立::

    layouts/_meta.json       # {"layouts": ["android", ...]} 布局顺序
    layouts/{key}.yaml       # 布局清单条目（name/desc/env/canvas/aspect/extends）
    layouts/{key}/{group}/{scene}.json   # 坐标，布局内再按场景分组拆子目录

合并语义与场景侧同一 namespace 优先级链（``system > local``）：

- 布局列表 = system 区间 + local 区间；local ``_meta.json`` 充当排序补丁，
  区间之间永不交错。
- 布局清单条目：system 为基底，local 同 key 文件按**字段级补丁**深合并
  （屏幕标定只覆盖 ``canvas``，不冻住其它字段的系统更新）；local 独有 key
  即用户新建布局，整文件生效。

Layout keys are stable storage identities.  Display names are metadata and must
never be used to construct paths or persisted references.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ..config.resolver import ConfigResolver, get_resolver
from ..reserved_names import ensure_name_not_reserved

if TYPE_CHECKING:
    from .models import Layout

LAYOUTS_SCHEMA_VERSION = 3
LAYOUT_META_REL = "layouts/_meta.json"
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


def canvas_aspect_matches(
    layout: Layout | None,
    width: float,
    height: float,
    *,
    allow_portrait: bool = False,
) -> tuple[bool, bool]:
    """判断截图尺寸与布局声明的画布宽高比是否相容。

    返回 ``(是否通过, 是否由交换宽高后的竖屏匹配通过)``。``allow_portrait``
    为真时，正常比较失败后会把宽高互换，再按同一套画布比例与容差比较；
    只有交换后通过才标记为竖屏匹配。未声明宽高比、尺寸非法时放行——门禁
    只拦已知错配，不拦「还不知道」。
    """
    if layout is None or not layout.aspect or width <= 0 or height <= 0:
        return True, False
    try:
        expected = parse_aspect_ratio(layout.aspect)
    except ValueError:
        return True, False
    if expected is None:
        return True, False
    canvas = layout.canvas
    deviation = canvas_aspect_deviation(
        expected, canvas.w_ratio * width, canvas.h_ratio * height)
    if deviation is None or deviation <= layout.aspect_tolerance:
        return True, False
    if allow_portrait:
        swapped = canvas_aspect_deviation(
            expected, canvas.w_ratio * height, canvas.h_ratio * width)
        if swapped is not None and swapped <= layout.aspect_tolerance:
            return True, True
    return False, False


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
    #: 适配环境（推荐值）：取 app.yaml ``envs`` 的环境 key（android/desktop）。
    #: 只作消费者过滤与提示用，**不参与运行环境判定**——env() 仍以实际运行
    #: 环境为准；布局声明与运行环境不符时由消费者（如手机下发）自行拦截。
    #: 空串表示未声明（旧配置），消费者不得据此隐藏布局。
    env: str = ""

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
    # 语法合法不代表可用：截图目录按布局 key 建子目录，与任务包的
    # ``screenshots/packages/`` 同级的名字会互相踩。
    return ensure_name_not_reserved(value, kind="布局")


# ─── 目录化清单读取 ──────────────────────────────────────

def _read_meta_order(layouts_dir) -> list[str]:
    """读一层 ``layouts/_meta.json``；缺失/损坏返回空列表（降级为文件名序）。"""
    path = layouts_dir / "_meta.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = data.get("layouts")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError("layouts 必须是字符串列表")
        return list(keys)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"layouts/_meta.json 解析失败，该层顺序降级为文件名序: {path}: {e}")
        return []


def _apply_order_patch(keys: list[str], owner_keys: list[str],
                       patch: list[str]) -> list[str]:
    """排序补丁：补丁中属于本区块的 key 按补丁顺序在前，其余按原顺序在后。"""
    mine = set(owner_keys)
    patched = [k for k in patch if k in mine]
    patched.extend(k for k in keys if k not in patched)
    return patched


def _layer_layout_keys(layouts_dir) -> list[str]:
    """一层的布局 key 清单：meta 顺序 ∪ ``{layouts}/*.yaml``（并集稳定追加）。"""
    if not layouts_dir.is_dir():
        return []
    found = list(_read_meta_order(layouts_dir))
    for path in sorted(layouts_dir.glob("*.yaml")):
        key = path.stem
        if not key.startswith("_") and key not in found:
            found.append(key)
    return [k for k in found if (layouts_dir / f"{k}.yaml").is_file()]


def _load_entry_file(root, key: str) -> dict | None:
    import yaml
    path = root / f"{key}.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.error(f"布局清单解析失败 {path}: {e}")
        return None
    return data if isinstance(data, dict) else None


def _merge_entry(base: dict, patch: dict) -> dict:
    """字段级补丁：dict 字段（canvas）递归合并，标量整键替换。"""
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_entry(result[key], value)
        else:
            result[key] = value
    return result


def layout_manifest_rel(key: str) -> str:
    """布局清单文件相对路径。"""
    return f"layouts/{validate_layout_key(key)}.yaml"


def load_layout_doc(resolver: ConfigResolver | None = None) -> dict:
    """合并视图：system 区间在前、local 区间在后；local 同 key 字段级补丁。"""
    resolver = resolver or get_resolver()
    system_dir, local_dir = resolver.system_dir, resolver.local_dir
    system_layouts = system_dir / "layouts"
    local_layouts = local_dir / "layouts"
    local_patch_order: list[str] = (
        _read_meta_order(local_layouts) if local_layouts != system_layouts else [])

    entries: dict[str, dict] = {}
    ordered_keys: list[str] = []

    # system 区间
    system_keys = _layer_layout_keys(system_layouts)
    system_keys = _apply_order_patch(system_keys, system_keys, local_patch_order)
    for key in system_keys:
        entry = _load_entry_file(system_layouts, key)
        if entry is None:
            continue
        if local_layouts != system_layouts:
            patch = _load_entry_file(local_layouts, key)
            if patch is not None:
                entry = _merge_entry(entry, patch)
        entries[key] = entry
        ordered_keys.append(key)

    # local 区间（local 独有布局）
    if local_layouts != system_layouts:
        local_keys = [k for k in _layer_layout_keys(local_layouts) if k not in entries]
        local_keys = _apply_order_patch(local_keys, local_keys, local_patch_order)
        for key in local_keys:
            entry = _load_entry_file(local_layouts, key)
            if entry is None:
                continue
            entries[key] = entry
            ordered_keys.append(key)

    return {"schema_version": LAYOUTS_SCHEMA_VERSION,
            "layouts": {k: entries[k] for k in ordered_keys}}


def parse_layout_entries(doc: dict) -> dict[str, LayoutEntry]:
    if not isinstance(doc, dict):
        raise ValueError("布局合并视图必须是映射")
    raw_layouts = doc.get("layouts")
    if not isinstance(raw_layouts, dict):
        raise ValueError("布局合并视图的 layouts 必须是映射")

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
        env_raw = raw_entry.get("env", "")
        if isinstance(env_raw, bool):
            raise ValueError(f"布局 {key} 的 env 不能是布尔值")
        env = ("" if env_raw is None
               else str(env_raw).strip() if isinstance(env_raw, str)
               else str(env_raw))
        if env:
            try:
                validate_layout_key(env)
            except ValueError:
                raise ValueError(
                    f"布局 {key} 的 env 必须是环境 key（app.yaml envs 的小写"
                    f"标识，如 android / desktop）: {env!r}") from None
        entries[key] = LayoutEntry(
            key, name, desc, canvas, extends, aspect, tolerance, env)

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

    # env 是推荐值：别名布局跟随根布局的目标环境，不要求逐条重复声明。
    for key, entry in entries.items():
        if entry.extends and not entry.env:
            parent = entries[entry.extends]
            if parent.env:
                entries[key] = dataclasses.replace(entry, env=parent.env)
    return entries


def load_layout_entries(
    resolver: ConfigResolver | None = None,
) -> dict[str, LayoutEntry]:
    """合并视图的布局条目；插入顺序即 effective 顺序（system 区间在前）。"""
    return parse_layout_entries(load_layout_doc(resolver))


def layouts_for_env(
    entries: dict[str, LayoutEntry], env: str,
) -> dict[str, LayoutEntry]:
    """按适配环境过滤布局（env 是推荐值，不是运行环境判定）。

    - 声明了 env 且等于目标的：入选；
    - 声明了 env 且不同的：剔除（手机下发据此挡掉桌面布局）；
    - 未声明 env 的（旧配置 / 用户自建未补）：保留——不因信息缺失隐藏布局。
    """
    return {key: entry for key, entry in entries.items()
            if not entry.env or entry.env == env}


# ─── 保存 ────────────────────────────────────────────────

def _dump_entry(entry: dict) -> str:
    import yaml
    return yaml.safe_dump(entry, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)


def save_layout_doc(doc: dict, resolver: ConfigResolver | None = None) -> None:
    """把合并视图按 key 落盘（条目写入 ``layouts/{key}.yaml``）。

    - 开发模式：全部条目写 system；
    - 用户模式：system 布局写字段级补丁到 local（补丁为空则删除 local 覆盖）；
      local 独有布局整文件写 local。

    入参应是 :func:`load_layout_doc` 返回的完整合并视图；缺失的 key 不会被动
    删除（删除走 ``delete_layout`` 显式路径）。
    """
    resolver = resolver or get_resolver()
    parse_layout_entries(doc)
    raw_layouts = doc.get("layouts") or {}
    system_dir, local_dir = resolver.system_dir, resolver.local_dir
    user_mode = not resolver.is_dev_mode()
    for key, entry in raw_layouts.items():
        validate_layout_key(key)
        system_entry = _load_entry_file(system_dir, key)
        if user_mode and system_entry is not None:
            from ..config.merge import compute_diff
            patch = compute_diff(system_entry, entry)
            local_path = local_dir / f"{key}.yaml"
            if patch:
                resolver.write_entity(f"layouts/{key}.yaml", _dump_entry(patch))
            elif local_path.exists():
                resolver.delete_entity(f"layouts/{key}.yaml")
            continue
        # dev 模式全部写 system；用户模式只写 local 独有布局（整文件）
        resolver.write_entity(f"layouts/{key}.yaml", _dump_entry(entry))


def save_layout_order(order: list[str],
                      resolver: ConfigResolver | None = None) -> None:
    """写布局顺序 ``layouts/_meta.json``（dev → system；user → local 排序补丁）。"""
    resolver = resolver or get_resolver()
    text = json.dumps({"layouts": list(order)}, ensure_ascii=False, indent=2) + "\n"
    resolver.write_entity(LAYOUT_META_REL, text)


def is_system_layout_key(key: str,
                         resolver: ConfigResolver | None = None) -> bool:
    """布局是否由 system 层声明（清单文件的单一事实源）。"""
    resolver = resolver or get_resolver()
    return (resolver.system_dir
            / layout_manifest_rel(key)).is_file()


def save_layout_entry_patch(key: str, fields: dict,
                            resolver: ConfigResolver | None = None) -> None:
    """只覆盖一个 system 布局的少数字段（屏幕标定的 canvas 等）。

    用户模式写字段级补丁到 local（与 system 值一致则删覆盖文件）；
    开发模式直接写完整条目到 system。
    """
    resolver = resolver or get_resolver()
    key = validate_layout_key(key)
    entries = load_layout_entries(resolver)
    entry = entries.get(key)
    if entry is None:
        raise ValueError(f"布局 key 不存在: {key}")
    merged = load_layout_doc(resolver).get("layouts", {}).get(key) or {}
    merged.update(fields)
    if resolver.is_dev_mode():
        resolver.write_entity(f"layouts/{key}.yaml", _dump_entry(merged))
        return
    system_entry = _load_entry_file(resolver.system_dir, key)
    if system_entry is None:
        # local 独有布局：整文件更新
        resolver.write_entity(f"layouts/{key}.yaml", _dump_entry(merged))
        return
    from ..config.merge import compute_diff
    patch = compute_diff(system_entry, merged)
    local_path = resolver.local_dir / f"{key}.yaml"
    if patch:
        resolver.write_entity(f"layouts/{key}.yaml", _dump_entry(patch))
    elif local_path.exists():
        resolver.delete_entity(f"layouts/{key}.yaml")
