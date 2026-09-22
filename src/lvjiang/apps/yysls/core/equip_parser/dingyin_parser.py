"""定音词条解析器

解析装备详情场景（equip_weapon_detail / equip_armor_detail）OCR 的
dingyin 字段文本，产出 {"name": 原始词条名, "value": 数值}。

定音词条名全局唯一（增益类 外功穿透/外功抗性/属攻穿透 与
指定技能增效类 十大流派×5 条互不重叠），故匹配无需依赖装备部位，
直接用全量候选池——避免 equip_type OCR 漏读时连带定音解析失败。

候选词条名动态取自 GameConfigManager（attributes.yaml 的 _aliases），
UI 增删定音词条后无需改代码。

一件装备可以同时定着普通定音和止戈定音，游戏里随时无成本切换，所以两者
各占一个数据槽：``dingyin`` 存普通定音，``dingyin_zhige`` 存止戈定音。
``dingyin_type`` 记录扫描时装备展示的是哪一种，只影响展示，不影响计算。

无法按普通定音词库解析的文本一律按游戏中可预计的「止戈定音」处理。它不是
装备异常，不进入 ``illegal_equip``；``_extra.is_zhige_dingyin`` 作为派生标记
保留，供尚未改造的读取方继续判断。
"""

import re

from loguru import logger

# 定音词条所在的全部类别（增益类 + 指定技能增效）
#
# 传给 get_aliases_for_category()，比对的是 attributes.yaml 里 affix_caps
# 段的分类 key——那是裸中文的游戏配置数据，从不过 tr()，这里也绝不能
# 过 tr()，否则英文界面下会用翻译后的英文去匹配裸中文 key，查不到任何
# 别名，定音词条会整体解析失败。
_DINGYIN_CATEGORIES = ("外功增益", "属攻增益", "指定技能增效")

# _extra 中的止戈定音标记；与 illegal_equip 完全独立。
# 它现在是 dingyin_type 的派生值，不再是权威来源——保留是为了让还没改造的
# 读取方（卡片、承音合并资格）零改动继续工作。
ZHIGE_DINGYIN_KEY = "is_zhige_dingyin"
# 装备当前展示的定音种类。缺失按 normal 读，见 resolve_dingyin_type()。
DINGYIN_TYPE_KEY = "dingyin_type"
DINGYIN_NORMAL = "normal"
DINGYIN_ZHIGE = "zhige"
DINGYIN_TYPES = (DINGYIN_NORMAL, DINGYIN_ZHIGE)
# 止戈定音数据槽。真实止戈词条尚未进词库，名称先固定，结构与 dingyin 对齐，
# 将来能解析出真实名称和数值时直接往里填，消费方不用改。
DINGYIN_ZHIGE_KEY = "dingyin_zhige"
ZHIGE_DINGYIN_NAME = "止戈定音"
# 非普通定音疑似 OCR 误读时的说明；不使用装备异常小标记，卡片仍直接展示
# <止戈定音>，说明仅作为该行的提示信息。
DINGYIN_NOTICE_KEY = "dingyin_notice"


def is_zhige_dingyin(equip_dict: dict) -> bool:
    """装备是否带有止戈定音标记。"""
    return bool((equip_dict.get("_extra") or {}).get(ZHIGE_DINGYIN_KEY))


def has_normal_dingyin(equip_dict: dict) -> bool:
    """普通定音槽是否有数据。"""
    dingyin = equip_dict.get("dingyin")
    return isinstance(dingyin, dict) and bool(dingyin.get("name"))


def has_zhige_dingyin(equip_dict: dict) -> bool:
    """止戈定音槽是否有数据。"""
    zhige = equip_dict.get(DINGYIN_ZHIGE_KEY)
    return isinstance(zhige, dict) and bool(zhige.get("name"))


def has_any_dingyin(equip_dict: dict) -> bool:
    """这件装备定过音没有——两种都算。

    历史记录只有 ``is_zhige_dingyin`` 标记、还没补出止戈数据槽，同样算数：
    资格判定不该依赖调用方有没有先走过一次加载期刷新。
    """
    return (has_normal_dingyin(equip_dict) or has_zhige_dingyin(equip_dict)
            or stored_dingyin_type(equip_dict) == DINGYIN_ZHIGE)


def can_switch_dingyin(equip_dict: dict) -> bool:
    """能否切换定音：两个槽都有数据才谈得上切换。

    展示切换标记、启用切换按钮、接受切换写入都问这一个函数；分开判定迟早
    分叉成「有标记但点不动」。
    """
    return has_normal_dingyin(equip_dict) and has_zhige_dingyin(equip_dict)


def stored_dingyin_type(equip_dict: dict) -> str:
    """记录里存着的定音种类；历史记录只按旧的止戈标记推定。

    旧实现还会按「名称能不能被词库解释」反推止戈。这里不再那样做：名称判定
    会随定音词库配置增删而翻转，不该决定一个已经存成事实的展示状态。历史上
    被写进普通定音槽的止戈名称重扫一次就会归位。
    """
    kind = str(equip_dict.get(DINGYIN_TYPE_KEY) or "")
    if kind in DINGYIN_TYPES:
        return kind
    if (equip_dict.get("_extra") or {}).get(ZHIGE_DINGYIN_KEY):
        return DINGYIN_ZHIGE
    return DINGYIN_NORMAL


def resolve_dingyin_type(equip_dict: dict) -> str:
    """展示时该按哪种定音。

    存的那一侧没有数据时回落到有数据的一侧——能显示的东西永远不会错，硬按
    一个空槽展示会让定音整行凭空消失。
    """
    kind = stored_dingyin_type(equip_dict)
    if kind == DINGYIN_ZHIGE and not has_zhige_dingyin(equip_dict):
        return DINGYIN_NORMAL if has_normal_dingyin(equip_dict) else kind
    if kind == DINGYIN_NORMAL and not has_normal_dingyin(equip_dict):
        return DINGYIN_ZHIGE if has_zhige_dingyin(equip_dict) else kind
    return kind


def refresh_dingyin_marker_dict(equip_dict: dict) -> bool:
    """按 dingyin_type 刷新止戈标记与历史记录的止戈槽，返回是否展示止戈。

    标记现在是 ``dingyin_type`` 的派生值。绝不能反过来从 ``dingyin.name``
    推标记——新模型下普通定音槽会长期保留数据，那样每次加载都会把止戈态
    改回普通态。历史记录在这里补出 ``dingyin_zhige`` 展示槽，不改磁盘格式。
    """
    kind = stored_dingyin_type(equip_dict)
    if kind == DINGYIN_ZHIGE and not has_zhige_dingyin(equip_dict):
        equip_dict[DINGYIN_ZHIGE_KEY] = {"name": ZHIGE_DINGYIN_NAME}
    raw_extra = equip_dict.get("_extra")
    extra = raw_extra if isinstance(raw_extra, dict) else {}
    if kind == DINGYIN_ZHIGE:
        equip_dict["_extra"] = extra
        extra[ZHIGE_DINGYIN_KEY] = True
    else:
        extra.pop(ZHIGE_DINGYIN_KEY, None)
    return kind == DINGYIN_ZHIGE


class DingyinParser:
    """定音词条解析器"""

    def __init__(self):
        from ...config import get_game_config
        self._attr_config = get_game_config()

    def parse(self, raw: str) -> dict | None:
        """解析定音文本

        Args:
            raw: OCR 定音文本（如 "外功穿透 +14.2%"、"无名剑法武学技增伤+8.0%"）

        Returns:
            {"name": 原始词条名, "value": float} 或 None（为空 / 无法识别）
        """
        # 输入应由 OCR 引擎清洗，此处直接使用
        text = raw.strip() if raw else ""
        if not text:
            return None

        matched = self._match_name(text, self._candidates())
        if matched is None:
            logger.debug(f"定音不属于普通词库: {raw!r}")
            return None

        value = self._extract_value(text, matched)
        if value is None:
            logger.warning(f"定音数值无法提取: {raw!r}")
            return None

        return {"name": matched, "value": value}

    def matched_name(self, raw: str) -> str | None:
        """文本命中的普通定音名称；不要求数值可解析。"""
        text = raw.strip() if raw else ""
        return self._match_name(text, self._candidates()) if text else None

    def matches_normal_name(self, raw: str) -> bool:
        """文本是否包含一个已配置的普通定音名称（不要求数值可解析）。"""
        return self.matched_name(raw) is not None

    #: 判定「疑似误读」的最短公共前缀长度。定音名多为 4-6 个汉字，
    #: 共享 3 字前缀已足够区分「OCR 错了一两个字」与「压根是另一种词条」。
    MISREAD_PREFIX_MIN = 3

    def suspected_misread(self, raw: str) -> str | None:
        """文本疑似哪个普通定音的 OCR 误读；不像误读则返回 None

        止戈定音与 OCR 乱码在形态上无法区分（都带数值、名称都不在词库里），
        但误读会与真实词条名共享一段长前缀——「外功穿诱」对「外功穿透」共享
        「外功穿」。据此把两者分开：像误读的报 warning 交用户校正，
        不像的才按可预计的止戈定音处理。
        """
        text = raw.strip() if raw else ""
        if not text or self._match_name(text, self._candidates()):
            return None
        best: str | None = None
        best_len = self.MISREAD_PREFIX_MIN - 1
        for name in self._candidates():
            shared = 0
            for a, b in zip(text, name, strict=False):
                if a != b:
                    break
                shared += 1
            if shared > best_len:
                best, best_len = name, shared
        return best

    def _candidates(self) -> list[str]:
        """全量定音候选词条名（长度降序，保证最长优先匹配）"""
        names: list[str] = []
        for cat in _DINGYIN_CATEGORIES:
            names.extend(self._attr_config.get_aliases_for_category(cat))
        return sorted(names, key=len, reverse=True)

    @staticmethod
    def _match_name(text: str, candidates: list[str]) -> str | None:
        """匹配候选词条名：前缀优先，其次子串（容忍 OCR 前缀噪声）"""
        for name in candidates:
            if text.startswith(name):
                return name
        for name in candidates:
            if name in text:
                return name
        return None

    @staticmethod
    def _extract_value(text: str, matched: str) -> float | None:
        """从词条名之后的剩余文本提取数值"""
        remainder = text.split(matched, 1)[1]
        m = re.search(r"(\d+\.?\d*)", remainder)
        return float(m.group(1)) if m else None


# ─── 全局单例 ─────────────────────────────────────────────

_dingyin_parser_instance: DingyinParser | None = None


def get_dingyin_parser() -> DingyinParser:
    """获取全局 DingyinParser 单例"""
    global _dingyin_parser_instance
    if _dingyin_parser_instance is None:
        _dingyin_parser_instance = DingyinParser()
    return _dingyin_parser_instance
