"""定音词条解析器

解析装备详情场景（equip_weapon_detail / equip_armor_detail）OCR 的
dingyin 字段文本，产出 {"name": 原始词条名, "value": 数值}。

定音词条名全局唯一（增益类 外功穿透/外功抗性/属攻穿透 与
指定技能增效类 十大流派×5 条互不重叠），故匹配无需依赖装备部位，
直接用全量候选池——避免 equip_type OCR 漏读时连带定音解析失败。

候选词条名动态取自 GameConfigManager（attributes.yaml 的 _aliases），
UI 增删定音词条后无需改代码。
"""

import re

from loguru import logger

from .cleaner import clean_affix_text

# 定音词条所在的全部类别（增益类 + 指定技能增效）
_DINGYIN_CATEGORIES = ("外功增益", "属攻增益", "指定技能增效")


class DingyinParser:
    """定音词条解析器"""

    def __init__(self):
        from ..game_config import get_game_config
        self._attr_config = get_game_config()

    def parse(self, raw: str) -> dict | None:
        """解析定音文本

        Args:
            raw: OCR 定音文本（如 "外功穿透 +14.2%"、"无名剑法武学技增伤+8.0%"）

        Returns:
            {"name": 原始词条名, "value": float} 或 None（为空 / 无法识别）
        """
        # 数据清洗（与普通词条同一套规则：误识别替换 + 噪声字符删除）
        text = clean_affix_text(raw)
        # 游戏内定音显示为 武学·技能（含间隔号），配置词条名为连写形态，去除后匹配
        text = text.replace("·", "")
        if not text:
            return None

        matched = self._match_name(text, self._candidates())
        if matched is None:
            logger.warning(f"定音词条无法识别: {raw!r}")
            return None

        value = self._extract_value(text, matched)
        if value is None:
            logger.warning(f"定音数值无法提取: {raw!r}")
            return None

        return {"name": matched, "value": value}

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
