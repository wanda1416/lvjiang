"""定音词条解析器

解析装备详情场景（equip_weapon_detail / equip_armor_detail）OCR 的
dingyin 字段文本，产出 {"name": 原始词条名, "value": 数值}。

定音词条名全局唯一（增益类 外功穿透/外功抗性/属攻穿透 与
指定技能增效类 十大流派×5 条互不重叠），故匹配无需依赖装备部位，
直接用全量候选池——避免 equip_type OCR 漏读时连带定音解析失败。

候选词条名动态取自 GameConfigManager（attributes.yaml 的 _aliases），
UI 增删定音词条后无需改代码。

无法按普通定音词库解析的文本按游戏中可预计的「止戈定音」处理。它不是装备
异常，不进入 ``illegal_equip``，只在 ``_extra.is_zhige_dingyin`` 留下独立
标记，供 UI 在定音位置显示 ``<止戈定音>``。
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
ZHIGE_DINGYIN_KEY = "is_zhige_dingyin"
# 非普通定音疑似 OCR 误读时的说明；不使用装备异常小标记，卡片仍直接展示
# <止戈定音>，说明仅作为该行的提示信息。
DINGYIN_NOTICE_KEY = "dingyin_notice"


def is_zhige_dingyin(equip_dict: dict) -> bool:
    """装备是否带有止戈定音标记。"""
    return bool((equip_dict.get("_extra") or {}).get(ZHIGE_DINGYIN_KEY))


def refresh_dingyin_marker_dict(equip_dict: dict) -> bool:
    """根据已存定音刷新止戈标记，返回是否为止戈定音。

    普通定音名称能被词组配置识别时清除旧标记；未知定音名称标成止戈。
    若只有标记而没有 ``dingyin`` 数据（扫描时无法解析的典型形态），保留标记。
    本函数不读取、不展示、更不校验定音数值。
    """
    raw_extra = equip_dict.get("_extra")
    extra = raw_extra if isinstance(raw_extra, dict) else {}
    dingyin = equip_dict.get("dingyin")
    if not isinstance(dingyin, dict) or not dingyin.get("name"):
        return bool(extra.get(ZHIGE_DINGYIN_KEY))

    from ...config import get_game_config
    is_zhige = not get_game_config().is_dingyin_affix(str(dingyin["name"]))
    if is_zhige:
        equip_dict["_extra"] = extra
        extra[ZHIGE_DINGYIN_KEY] = True
    else:
        extra.pop(ZHIGE_DINGYIN_KEY, None)
        extra.pop(DINGYIN_NOTICE_KEY, None)
    return is_zhige


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

    def matches_normal_name(self, raw: str) -> bool:
        """文本是否包含一个已配置的普通定音名称（不要求数值可解析）。"""
        text = raw.strip() if raw else ""
        return bool(text and self._match_name(text, self._candidates()))

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
