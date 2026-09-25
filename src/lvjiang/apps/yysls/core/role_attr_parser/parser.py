"""角色基础属性 OCR 数据转换器

将角色详情页 detail_1（属性面板_左，反复滚动多屏）+ detail_2（属性面板_右，
点击"外功攻击"/"属性攻击"/"外功穿透"/"属攻穿透"后展开的详情）的 OCR 原始文本，解析成
"创建基础属性"对话框（`_CreatePlayStyleDialog`）能直接使用的 flat dict，
字段名对齐 `combat_attrs.COMBAT_ATTR_FIELDS`。

输入格式（由 scan_role_base_attr.wf 产出）：
    {
        "left_1": "武林造诣 | 2.445鹅 | ... | 五维属性 | ...",
        "left_2": "...",
        ...                              # 每屏一个 key，滚动顺序排列
        "right_outer_attack": "3769 外功攻击 | ... | 基础外功攻击：3769-3120 | ...",
        "right_attr_attack": "220-443 | 属性攻击 | ... | 鸣金攻击：170-343(...) | ...",
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

# detail_1 只有"当前流派"合并数值的百分比/单值字段，作为对应 detail_2 精确
# 数据缺失时的兜底（通用 key，见模块顶注释）
_CURRENT_PERCENT_FIELDS: dict[str, str] = {
    "外功穿透": "outer_pen",
    "属攻穿透": "attr_pen_current",
    "属攻伤害加成": "attr_bonus_current",
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
_BASE_OUTER_ATTACK_RE = re.compile(r"基础外功攻击[:：]\s*")
_SCHOOL_PEN_RE = re.compile(r"(鸣金|裂石|破竹|牵丝)穿透[:：]\s*(-?\d+\.?\d*)")
_OUTER_PEN_NON_DINGYIN_RE = re.compile(
    r"外功穿透[（(]非定音部分[）)][:：]\s*(-?\d+\.?\d*)"
)


_WHITESPACE_RE = re.compile(r"\s+")


def _squeeze(text: str) -> str:
    """删掉 OCR 在一行里插进来的全部空白。

    识别层会把 "51.1%" 读成 "51. 1%"、把 "36.0" 读成 "3 6.0"，这类空白没有
    语义，却让数值解析整条失败，字段被静默丢弃（见 v0.13.3 的会心率）。面板
    的标签和数值都不含有意义的空格，所以行内空白一律删除，而不是逐个字段写
    容错正则。

    代价是 OCR 同时漏掉小数点时（"51 1%"）会并成 "511%"。滚动扫描每屏独立
    解析、后一屏覆盖前一屏，这类单屏误读会被相邻屏的正确识别纠正。
    """
    return _WHITESPACE_RE.sub("", text)


def _to_float(text: str) -> float | None:
    text = _squeeze(text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _strip_percent_paren(value: str) -> float | None:
    """"114.2%(94.8%)" → 114.2（取括号外的"白字"数值）；"40.0%" → 40.0"""
    value = _squeeze(value).split("(", 1)[0]
    value = value.rstrip("%")
    return _to_float(value)


_RANGE_RE = re.compile(r"^(-?\d+)\s*-\s*(-?\d+)$")
# 恒定值：游戏内最小值 > 最大值时不显示区间，改成"箭头 + 单值"（如 "← 3713"）。
# 箭头符号 OCR 结果不稳定（← / ↑ 等变体都可能出现），用"非数字非负号前缀"兜底匹配。
_CONSTANT_VALUE_RE = re.compile(r"^[^\d-]*(-?\d+)$")


def _split_range(value: str) -> tuple[float | None, float | None]:
    """"900-2604" → (900.0, 2604.0)；"← 3713" → (3713.0, 3713.0)（恒定值兜底）"""
    value = _squeeze(value).replace("（", "(").replace("）", ")")
    value = value.split("(", 1)[0]
    m = _RANGE_RE.match(value)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _CONSTANT_VALUE_RE.match(value)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


def _warn_unparsed(label: str, value: str) -> None:
    """标签命中但数值解析不出来——必须留痕。

    以前这里静默跳过，结果是面板少一个字段、静默写入被门禁拒绝，日志里却
    没有任何线索指向具体是哪个字段的 OCR 出了问题。
    """
    logger.warning(f"角色属性识别: 标签 {label!r} 的数值无法解析: {value!r}")


def parse_detail1(tokens: list[str]) -> dict[str, float]:
    """解析单屏 detail_1 token 序列，提取已知字段的数值。

    命中已知标签 → 取下一个 token 做数值解析；命中不了的 token 跳过。
    标签和数值都先删掉行内空白，OCR 插进来的空格不影响匹配（见 _squeeze）。
    """
    result: dict[str, float] = {}
    tokens = [_squeeze(token) for token in tokens]
    n = len(tokens)
    for i, label in enumerate(tokens):
        if i + 1 >= n:
            continue
        value = tokens[i + 1]

        if label in _PERCENT_FIELDS:
            num = _strip_percent_paren(value)
            if num is not None:
                result[_PERCENT_FIELDS[label]] = num
            else:
                _warn_unparsed(label, value)
            continue

        if label in _RANGE_FIELDS:
            lo, hi = _split_range(value)
            min_field, max_field = _RANGE_FIELDS[label]
            if lo is not None:
                result[min_field] = lo
            else:
                _warn_unparsed(label, value)
            # 外功攻击在最小值超过最大值时，左区只显示箭头加最小值。
            # 这个单值不能证明最大值相同；最大值必须从右区“基础外功攻击”读取。
            if hi is not None and _RANGE_RE.match(value):
                result[max_field] = hi
            continue

        # 以下是"当前流派"合并数值，detail_2 精确数据缺失时的兜底
        if label == "属性攻击":
            lo, hi = _split_range(value)
            if lo is not None:
                result["min_attr_current"] = lo
            else:
                _warn_unparsed(label, value)
            if hi is not None:
                result["max_attr_current"] = hi
            continue
        if label in _CURRENT_PERCENT_FIELDS:
            num = _strip_percent_paren(value)
            if num is not None:
                # 无 detail_2 时的兜底，detail_2 的精确值会覆盖
                result[_CURRENT_PERCENT_FIELDS[label]] = num
            else:
                _warn_unparsed(label, value)
            continue

    return result


def parse_detail2_outer_attack(text: str) -> dict[str, float]:
    """解析“外功攻击”右区详情中的基础区间，保留最小值大于最大值的顺序。"""
    text = _squeeze(text or "")
    match = _BASE_OUTER_ATTACK_RE.search(text)
    if not match:
        return {}
    value_part = text[match.end():].split("|", 1)[0]
    lo, hi = _split_range(value_part)
    result: dict[str, float] = {}
    if lo is not None:
        result["min_outer"] = lo
    if hi is not None and _RANGE_RE.match(value_part):
        result["max_outer"] = hi
    return result


def parse_detail2_attr_attack(text: str) -> dict[str, float]:
    """解析"属性攻击"detail_2 展开文本，提取四门武学 + 无相攻击的区间数值。

    "鸣金攻击：170-343(170-343)" → min_mingjin=170, max_mingjin=343
    "鸣金攻击：← 3713"（恒定值，min>max 时游戏改用箭头+单值展示）→
    min_mingjin=max_mingjin=3713。

    取值截止到标签匹配位置之后的第一个"|"（与 detail_1 按 "|" 切 token 的
    边界语义一致），交给 _split_range 解析，而不是直接在数值上写死
    "num-num" 正则，这样恒定值格式也能落到同一套解析逻辑，不需要为箭头
    格式单独写一份正则。
    """
    text = _squeeze(text or "")
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
    m = _OUTER_PEN_NON_DINGYIN_RE.search(_squeeze(text or ""))
    if not m:
        return {}
    return {"outer_pen": float(m.group(1))}


def parse_detail2_attr_pen(text: str) -> dict[str, float]:
    """解析"属攻穿透"detail_2 展开文本，提取四门武学的分项穿透数值。

    "鸣金穿透：10.3" → mingjin_pen=10.3（无相穿透无对应字段，忽略）

    游戏事实（登记于 docs/10-game/06-mechanics-conventions.md B6）：该详情页
    把装备定音的「无相穿透」单独列一行，**不**并入各流派分项；当前版本也没有
    「鸣金穿透」之类的流派穿透装备词条。所以这里读到的四个分项就是角色自身
    的基础穿透，可直接作为基础属性保存，不需要再扣装备。无相穿透如何计入
    流派属攻穿透由 ``combat_attrs.fold_wuxiang_pen`` 在计算时处理。
    """
    result: dict[str, float] = {}
    for m in _SCHOOL_PEN_RE.finditer(_squeeze(text or "")):
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
            raw: {"left_1": ..., "left_2": ..., "right_outer_attack": ...,
                  "right_attr_attack": ..., "right_outer_pen": ...,
                  "right_attr_pen": ...}

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
        if left_keys:
            # 各屏独立提取完整的标签和值；后一次有效识别可纠正前一屏 OCR 错字。
            # 不按重叠 token 裁剪，否则相同的数值可能误删新屏的正确标签。
            for key in left_keys:
                tokens = [t.strip() for t in raw[key].split("|") if t.strip()]
                result.update(parse_detail1(tokens))
        elif not any(k.startswith("right_") and raw.get(k) for k in raw):
            logger.warning("RoleAttrParser.parse: 未找到任何 left_* 快照")

        # detail_2 精确数据覆盖 detail_1 的兜底值。尤其外功攻击左区在
        # min > max 时只显示 min，不能用该单值推导 max。
        right_outer_attack = raw.get("right_outer_attack")
        if right_outer_attack:
            result.update(parse_detail2_outer_attack(right_outer_attack))

        right_attr_attack = raw.get("right_attr_attack")
        if right_attr_attack:
            result.update(parse_detail2_attr_attack(right_attr_attack))

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
