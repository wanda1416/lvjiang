"""角色基础属性 OCR 数据转换器

将角色详情页 detail_1（属性面板_左，反复滚动多屏）+ detail_2（属性面板_右，
点击"属性攻击"/"外功穿透"/"属攻穿透"后展开的详情）的 OCR 原始文本，解析成
"创建基础属性"对话框（`_CreatePlayStyleDialog`）能直接使用的 flat dict，
字段名对齐 `combat_attrs.COMBAT_ATTR_FIELDS`。

输入格式（由 scan_role_base_attr.wf 产出）：
    {
        "left_1": "武林造诣 | 2.445鹅 | ... | 五维属性 | ...",
        "left_2": "...",
        ...                              # 每屏一个 key，滚动顺序排列
        "right_attack": "220-443 | 属性攻击 | ... | 鸣金攻击：170-343(...) | ...",
        "right_outer_pen": "0.0 | 外功穿透 | ... 当前外功穿透(非定音部分)：0.0 | ...",
        "right_attr_pen": "10.3 | 属攻穿透 | ... 鸣金穿透：10.3 | ...",
    }

输出格式：{field_name: float}，field_name 见 COMBAT_ATTR_FIELDS，另含四个
"当前流派"通用兜底 key（min_attr_current/max_attr_current/attr_pen_current/
attr_bonus_current，供对话框按当前流派解析 __min_attr__ 等占位符时兜底）。

关于非本流派残留数值：detail_2 展开的"属性攻击/属攻穿透"面板会展示全部
四门武学的分项数值（角色装备词条可能带有非本流派属攻，如裂石流派角色
装备恰好有牵丝词条），本模块原样把 min_mingjin/min_qiansi/min_pozhu 等
全部具体字段解析出来，不在这里猜测/过滤"哪个是当前流派"——这个判断
交给 `_CreatePlayStyleDialog`（按 school_attr 只解析出当前流派对应的
一个具体字段名，其余字段名不会被读取/持久化，详见该类
`_resolve_initial_values` 的安全性说明）。
"""

import re

from loguru import logger

# detail_1 里"标签文本 → COMBAT_ATTR_FIELDS 字段名"映射（单值/无分流派拆分字段）
# 只收录 PLAY_STYLE_FIELD_GROUPS 实际用到的字段，其余标签一律忽略。
_PERCENT_FIELDS: dict[str, str] = {
    "精准率": "precision",
    "会心率": "crit_rate",
    "会意率": "intent_rate",
    "直接会心率": "direct_crit",
    "直接会意率": "direct_intent",
    "会心伤害加成": "crit_dmg",
    "会意伤害加成": "intent_dmg",
    "外功伤害加成": "outer_bonus",
}

# 区间字段（"900-2604" 这种 min-max 格式）
_RANGE_FIELDS: dict[str, tuple[str, str]] = {
    "外功攻击": ("min_outer", "max_outer"),
}

# 流派名 → 字段后缀，四门武学通用
_SCHOOL_SUFFIX = {
    "鸣金": "mingjin", "裂石": "lieshi", "破竹": "pozhu", "牵丝": "qiansi",
}

_SCHOOL_ATTACK_LABEL_RE = re.compile(r"(鸣金|裂石|破竹|牵丝|无相)攻击[:：]\s*")
_SCHOOL_PEN_RE = re.compile(r"(鸣金|裂石|破竹|牵丝)穿透[:：]\s*(-?\d+\.?\d*)")
_OUTER_PEN_NON_DINGYIN_RE = re.compile(r"外功穿透\(非定音部分\)[:：]\s*(-?\d+\.?\d*)")


def _to_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _strip_percent_paren(value: str) -> float | None:
    """"114.2%(94.8%)" → 114.2（取括号外的"白字"数值）；"40.0%" → 40.0"""
    value = value.split("(", 1)[0].strip()
    value = value.rstrip("%").strip()
    return _to_float(value)


_RANGE_RE = re.compile(r"^(-?\d+)\s*-\s*(-?\d+)$")
# 恒定值：游戏内最小值 > 最大值时不显示区间，改成"箭头 + 单值"（如 "← 3713"）。
# 箭头符号 OCR 结果不稳定（← / ↑ 等变体都可能出现），用"非数字非负号前缀"兜底匹配。
_CONSTANT_VALUE_RE = re.compile(r"^[^\d-]*(-?\d+)$")


def _split_range(value: str) -> tuple[float | None, float | None]:
    """"900-2604" → (900.0, 2604.0)；"← 3713" → (3713.0, 3713.0)（恒定值兜底）"""
    value = value.split("(", 1)[0].strip()
    m = _RANGE_RE.match(value)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _CONSTANT_VALUE_RE.match(value)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


def merge_scroll_snapshots(snapshots: list[str]) -> list[str]:
    """把多屏 OCR 文本按 token（"|" 分隔）去重拼接成一份连续序列。

    滚动距离为半屏，相邻两屏天然有约一半重叠内容：在新一屏 token 列表里
    搜索"已合并列表的后缀"作为连续子串出现的位置（不要求从新一屏开头
    对齐——滚动边界上的表头/行首文字经常被 OCR 裁出不同的残片，如
    "体劲御敏势" 滚到只剩 "敏势" 可见，新一屏开头会多出这类噪声 token），
    命中后只把匹配窗口之后的部分接上去，窗口之前的残片视为已见内容丢弃。
    某一屏与上一屏完全相同（原地没滚动/多滚一次触底）时，整屏被判定为
    全量重叠，不产生任何新增 —— 天然幂等，可直接喂多份重复文本。
    找不到任何重叠时退化为整屏直接拼接（不静默丢数据），并记 warning。
    """
    merged: list[str] = []
    for snap in snapshots:
        if not snap:
            continue
        tokens = [t.strip() for t in snap.split("|") if t.strip()]
        if not tokens:
            continue
        if not merged:
            merged = tokens
            continue

        max_k = min(len(merged), len(tokens))
        matched_end = None
        for k in range(max_k, 0, -1):
            suffix = merged[-k:]
            for start in range(len(tokens) - k + 1):
                if tokens[start:start + k] == suffix:
                    matched_end = start + k
                    break
            if matched_end is not None:
                break

        if matched_end is None:
            logger.warning(
                f"merge_scroll_snapshots: 未找到重叠，直接拼接（可能丢失连续性）: {tokens[:3]}..."
            )
            merged.extend(tokens)
        else:
            merged.extend(tokens[matched_end:])
    return merged


def parse_detail1(tokens: list[str]) -> dict[str, float]:
    """解析合并去重后的 detail_1 token 序列，提取已知字段的数值。

    命中已知标签 → 取下一个 token 做数值解析；命中不了的 token 跳过。
    """
    result: dict[str, float] = {}
    n = len(tokens)
    for i, label in enumerate(tokens):
        if i + 1 >= n:
            continue
        value = tokens[i + 1]

        if label in _PERCENT_FIELDS:
            num = _strip_percent_paren(value)
            if num is not None:
                result[_PERCENT_FIELDS[label]] = num
            continue

        if label in _RANGE_FIELDS:
            lo, hi = _split_range(value)
            min_field, max_field = _RANGE_FIELDS[label]
            if lo is not None:
                result[min_field] = lo
            if hi is not None:
                result[max_field] = hi
            continue

        # 属性攻击/外功穿透/属攻穿透/属攻伤害加成：detail_1 只有"当前流派"合并
        # 数值，作为对应 detail_2 精确数据缺失时的兜底（通用 key，见模块顶注释）
        if label == "属性攻击":
            lo, hi = _split_range(value)
            if lo is not None:
                result["min_attr_current"] = lo
            if hi is not None:
                result["max_attr_current"] = hi
            continue
        if label == "外功穿透":
            num = _strip_percent_paren(value)
            if num is not None:
                result["outer_pen"] = num  # 无 detail_2 时的兜底，detail_2 会覆盖
            continue
        if label == "属攻穿透":
            num = _strip_percent_paren(value)
            if num is not None:
                result["attr_pen_current"] = num
            continue
        if label == "属攻伤害加成":
            num = _strip_percent_paren(value)
            if num is not None:
                result["attr_bonus_current"] = num
            continue

    return result


def parse_detail2_attack(text: str) -> dict[str, float]:
    """解析"属性攻击"detail_2 展开文本，提取四门武学 + 无相攻击的区间数值。

    "鸣金攻击：170-343(170-343)" → min_mingjin=170, max_mingjin=343
    "鸣金攻击：← 3713"（恒定值，min>max 时游戏改用箭头+单值展示）→ min_mingjin=
    max_mingjin=3713，与 parse_detail1 里"外功攻击"共用 _split_range 的恒定值兜底。

    取值截止到标签匹配位置之后的第一个"|"（与 detail_1 按 "|" 切 token 的
    边界语义一致），交给 _split_range 解析，而不是直接在数值上写死
    "num-num" 正则，这样恒定值格式也能落到同一套解析逻辑，不需要为箭头
    格式单独写一份正则。
    """
    text = text or ""
    result: dict[str, float] = {}
    for m in _SCHOOL_ATTACK_LABEL_RE.finditer(text):
        name = m.group(1)
        suffix = _SCHOOL_SUFFIX.get(name, "wuxiang" if name == "无相" else None)
        if suffix is None:
            continue
        value_part = text[m.end():].split("|", 1)[0]
        lo, hi = _split_range(value_part)
        if lo is not None:
            result[f"min_{suffix}"] = lo
        if hi is not None:
            result[f"max_{suffix}"] = hi
    return result


def parse_detail2_outer_pen(text: str) -> dict[str, float]:
    """解析"外功穿透"detail_2 展开文本，取"(非定音部分)"数值。"""
    m = _OUTER_PEN_NON_DINGYIN_RE.search(text or "")
    if not m:
        return {}
    return {"outer_pen": float(m.group(1))}


def parse_detail2_attr_pen(text: str) -> dict[str, float]:
    """解析"属攻穿透"detail_2 展开文本，提取四门武学的分项穿透数值。

    "鸣金穿透：10.3" → mingjin_pen=10.3（无相穿透无对应字段，忽略）
    """
    result: dict[str, float] = {}
    for m in _SCHOOL_PEN_RE.finditer(text or ""):
        name, val = m.group(1), m.group(2)
        suffix = _SCHOOL_SUFFIX.get(name)
        if suffix is None:
            continue
        result[f"{suffix}_pen"] = float(val)
    return result


class RoleAttrParser:
    """角色基础属性 OCR 数据转换器"""

    def parse(self, raw: dict) -> dict[str, float]:
        """解析 scan_role_base_attr.wf 暂存的原始 OCR dict，返回 flat 数值字典。

        Args:
            raw: {"left_1": ..., "left_2": ..., "right_attack": ...,
                  "right_outer_pen": ..., "right_attr_pen": ...}

        Returns:
            {field_name: float}，可直接用于 `_CreatePlayStyleDialog` 预填。
        """
        if not isinstance(raw, dict) or not raw:
            logger.warning("RoleAttrParser.parse: 输入为空或非字典")
            return {}

        result: dict[str, float] = {}

        left_keys = sorted(
            (k for k in raw if k.startswith("left_") and raw.get(k)),
            key=lambda k: int(k.rsplit("_", 1)[-1]) if k.rsplit("_", 1)[-1].isdigit() else 0,
        )
        snapshots = [raw[k] for k in left_keys]
        if snapshots:
            tokens = merge_scroll_snapshots(snapshots)
            result.update(parse_detail1(tokens))
        else:
            logger.warning("RoleAttrParser.parse: 未找到任何 left_* 快照")

        # detail_2 精确数据覆盖 detail_1 的兜底值（仅 outer_pen 存在覆盖关系，
        # attack/attr_pen 是 detail_1 没有的分流派数据，只新增不覆盖）
        right_attack = raw.get("right_attack")
        if right_attack:
            result.update(parse_detail2_attack(right_attack))

        right_outer_pen = raw.get("right_outer_pen")
        if right_outer_pen:
            result.update(parse_detail2_outer_pen(right_outer_pen))

        right_attr_pen = raw.get("right_attr_pen")
        if right_attr_pen:
            result.update(parse_detail2_attr_pen(right_attr_pen))

        return result


# ─── 全局单例 ─────────────────────────────────────────────

_parser_instance: RoleAttrParser | None = None


def get_role_attr_parser() -> RoleAttrParser:
    """获取全局 RoleAttrParser 单例"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = RoleAttrParser()
    return _parser_instance
