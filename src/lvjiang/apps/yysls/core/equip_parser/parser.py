"""装备 OCR 数据转换器

将 OCR 原始识别的脏数据 dict 转换为标准装备领域模型结构。
"""

import re

from loguru import logger

from .....i18n import tr
from .constants import (
    AFFIX_NAMES,
    PERCENT_AFFIXES,
    WEAPON_TYPES,
    WUXUE_PATTERN,
    infer_category,
)
from .dingyin_parser import DingyinParser
from .models import Affix, EquipAttr, EquipmentData


class EquipmentParser:
    """装备 OCR 数据转换器"""

    # 部位单字 → 类型（类型段单字足以说明部位，容忍 OCR 错字）
    _PART_CHAR_TO_TYPE = {
        tr("冠"): tr("冠胄"), tr("胄"): "冠胄",
        tr("胸"): tr("胸甲"), tr("胫"): tr("胫甲"), tr("腕"): tr("腕甲"),
        tr("环"): "环", tr("佩"): "佩",
    }

    def __init__(self):
        from ...config import get_game_config
        self._attr_config = get_game_config()
        self._dingyin_parser = DingyinParser()

    def parse(self, raw: dict) -> EquipmentData:
        """解析单件装备的 OCR 原始数据

        从 equip_type 文本推断装备类别，决定解析路径。
        解析完成后自动推断品阶（quality）。

        Args:
            raw: OCR 原始 dict

        Returns:
            EquipmentData
        """
        # equip_type → name + type
        name, equip_type = self._parse_equip_type(raw.get("equip_type", ""))

        # 从 type 推断类别，决定 base_attr 解析路径
        category = infer_category(equip_type)

        equip = EquipmentData(type=equip_type, name=name)

        # equip_level → level + is_chengyin
        equip.level, equip.is_chengyin = self._parse_equip_level(
            raw.get("equip_level", "")
        )

        # base_attr：统一解析，不依赖 category
        equip.base_attr = self._parse_base_attr(raw.get("base_attr", ""))
        equip.base_attr_2 = self._parse_base_attr(raw.get("base_attr_2", ""))

        # 品阶推断：type + level + base_attr value → quality
        equip.quality = self._infer_quality(equip, category)

        # affixes（带级联脏数据丢弃）
        equip.affixes, affix_warnings = self._parse_affixes(raw)
        equip.warnings.extend(affix_warnings)

        # 计算词条数值百分比（cap_pct）
        self._calc_affix_cap_pct(equip)

        # 定音词条（词条不满 5 个时不可能有定音，跳过解析）
        dingyin_text = raw.get("dingyin", "").strip()
        if dingyin_text and len(equip.affixes) >= 5:
            result = self._dingyin_parser.parse(dingyin_text)
            if result:
                equip.dingyin = result
            else:
                equip.warnings.append(f"定音词条无法解析: {dingyin_text!r}")

        # 辅助信息
        equip.extra_data["affix_count"] = len(equip.affixes)

        return equip

    def _infer_quality(self, equip: EquipmentData, category: str) -> str | None:
        """根据 base_attr 推断品阶；等级/类型缺失时由数值反查回填

        基础属性值全局唯一，故即便 OCR 漏识别 equip_level，
        也能仅凭 base_attr 值反查出等级与品阶，并回填 equip.level；
        类型缺失（OCR 漏读/错字）时同样按数值反查回填 equip.type
        （仅数值唯一对应部位时回填；冠/胫/腕同值无法区分）。

        区间属性（武器）value 为 [min, max]，需与配置区间两端精确匹配；
        点值属性（首饰/防具）value 为标量。两者均直接透传。
        """
        if not equip.base_attr:
            return None
        value = equip.base_attr.value

        if equip.level:
            # 等级已知：常规按 (type, level, value) 推断品阶
            quality = self._attr_config.infer_quality(
                equip.type, equip.level, value)
        else:
            # 等级缺失（OCR 漏识别）：数值全局唯一 → 反查同时得到 等级+品阶
            level, quality = self._attr_config.infer_level_quality(
                equip.type, value)
            if level is not None:
                equip.level = level
                logger.info(
                    f"equip_level OCR 缺失，由基础属性值 {value} 反查得等级 {level}")

        # 类型缺失：数值反查回填部位（避免下游判定拿到 部位 None）
        if equip.type is None and quality:
            inferred = self._attr_config.infer_type_by_value(value)
            if inferred:
                equip.type = inferred
                logger.info(
                    f"equip_type OCR 缺失，由基础属性值 {value} 反查回填部位 {inferred}")
        return quality

    # ─── equip_type 解析 ──────────────────────────────────

    def _parse_equip_type(self, raw: str) -> tuple[str | None, str | None]:
        """解析装备类型字段

        格式：
            "踏雪含光 | 武器·剑"     → ("踏雪含光", "剑")
            "雁南飞冠 | 冠胄"        → ("雁南飞冠", "冠胄")
            "流星云珑"              → ("流星云珑", "环")  [从名称推断]
            "江无浪· | 一杆 | 武器·枪" → ("江无浪", "枪")  [脏]

        Returns:
            (name, type)
        """
        if not raw:
            return None, None

        parts = [p.strip() for p in raw.split("|") if p.strip()]

        if len(parts) == 1:
            # 无分隔符，只有名称
            name = parts[0].strip("· ")
            inferred_type = self._infer_type_from_name(name)
            return name, inferred_type

        # 名称取第一段，清理尾部残留符号
        name = parts[0].strip("· ")

        # 类型从最后一段提取
        last = parts[-1]
        weapon_type = self._extract_weapon_type(last)

        # 如果提取失败，尝试从名称推断
        if weapon_type is None:
            weapon_type = self._infer_type_from_name(name)

        return name, weapon_type

    def _infer_type_from_name(self, name: str) -> str | None:
        """从装备名称推断类型（固定规则）

        - 名称含"云珑" → 环
        - 名称含"辟邪" → 佩
        - 名称含"冠" → 冠胄（头部装备）
        - 名称含"胸" → 胸甲
        - 名称含"胫" → 胫甲
        - 名称含"腕" → 腕甲
        """
        if tr("云珑") in name:
            return tr("环")
        if tr("辟邪") in name:
            return tr("佩")
        # 防具类型推断（按名称中的关键字）
        if tr("冠") in name:
            return tr("冠胄")
        if tr("胸") in name:
            return tr("胸甲")
        if tr("胫") in name:
            return tr("胫甲")
        if tr("腕") in name:
            return tr("腕甲")
        return None

    def _extract_weapon_type(self, text: str) -> str | None:
        """从文本中提取武器类型或防具/首饰类别

        "武器·剑" → "剑"
        "冠胄"    → "冠胄"
        "胸申"    → "胸甲"  [OCR 错字，单字命中]
        "一杆"    → None（脏数据）
        """
        # 武器格式：武器·XX
        if tr("武器") in text:
            for wt in WEAPON_TYPES:
                if wt in text:
                    return wt
            return None

        # 防具/首饰：单字足以说明部位，容忍 OCR 错字
        for char, part_type in self._PART_CHAR_TO_TYPE.items():
            if char in text:
                return part_type

        return None

    # ─── equip_level 解析 ──────────────────────────────────

    def _parse_equip_level(self, raw: str) -> tuple[int | None, bool]:
        """解析装备等级

        "承音 | 110阶" → (110, True)
        "100阶"        → (100, False)
        ""             → (None, False)

        Returns:
            (level, is_chengyin)
        """
        raw = raw.strip()
        is_chengyin = tr("承音") in raw

        m = re.search(tr(r"(\d+)\s*阶"), raw)
        level = int(m.group(1)) if m else None

        return level, is_chengyin

    # ─── base_attr 解析 ────────────────────────────────────

    def _parse_base_attr(self, raw: str) -> EquipAttr | None:
        """统一解析 base_attr，尝试所有可能的格式

        支持格式：
          - 范围格式："外功攻击 87~203" → EquipAttr("外功攻击", [87, 203])
          - 单值格式："气血最大值 8750" → EquipAttr("气血最大值", 8750)
          - 脏数据："外功攻击 老著 52~121" → EquipAttr("外功攻击", [52, 121])
        """
        raw = raw.strip()
        if not raw:
            return None

        # 尝试范围格式（名称 + 数字~数字）
        # 名称：所有非数字字符（贪婪匹配，直到遇到数字）
        range_match = re.match(r"^([^\d]+)\s*(\d+)\s*~\s*(\d+)", raw)
        if range_match:
            name = range_match.group(1).strip()
            low = int(range_match.group(2))
            high = int(range_match.group(3))
            return EquipAttr(name=name, value=[low, high])

        # 尝试单值格式（名称 + 单个数字）
        # 已知属性名前缀
        for attr_name in [tr("气血最大值"), tr("外功防御"), tr("最大外功攻击"), tr("最小外功攻击")]:
            if raw.startswith(attr_name):
                remainder = raw[len(attr_name):]
                nums = re.findall(r"\d+", remainder)
                if nums:
                    return EquipAttr(name=attr_name, value=int(nums[-1]))
                return None

        # 无法匹配，尝试提取末尾数字作为单值
        nums = re.findall(r"\d+", raw)
        if nums:
            # 尝试提取名称（去掉数字和常见噪声字符）
            name_match = re.match(r"^([^\d]+)", raw)
            name = name_match.group(1).strip() if name_match else tr("未知")
            return EquipAttr(name=name, value=int(nums[-1]))

        logger.warning(f"base_attr 无法解析: {raw!r}")
        return None

    def _calc_affix_cap_pct(self, equip: EquipmentData):
        """计算每条词条的数值百分比（cap_pct）

        cap_pct = (value / cap) * 100，保留 1 位小数
        无对应上限数据时 cap_pct 保持 None
        """
        if not equip.level:
            return
        for affix in equip.affixes:
            caps = self._attr_config.get_affix_caps(equip.level, affix.name)
            if caps and caps.get("cap"):
                cap = caps["cap"]
                affix.cap_pct = round(affix.value / cap * 100, 1)

    def parse_affix_text(self, text: str, level: int | None = None) -> Affix | None:
        """解析单条词条文本（如调律结果弹窗的新词条）

        提供 level 时同步计算 cap_pct。无法解析返回 None。
        """
        affix = self._parse_single_affix(text)
        if affix and level:
            caps = self._attr_config.get_affix_caps(level, affix.name)
            if caps and caps.get("cap"):
                affix.cap_pct = round(affix.value / caps["cap"] * 100, 1)
        return affix

    # ─── affix 解析 ────────────────────────────────────────

    def _parse_affixes(
        self, raw: dict
    ) -> tuple[list[Affix], list[str]]:
        """解析 5 条词条，带级联脏数据丢弃

        规则：
        - 第 1 条（宫）必定出现
        - 第 2~4 条任一为空 → 后续全部丢弃
        - 第 5 条（羽）为空 → 正常结束（装备只有 4 条）

        Returns:
            (affixes, warnings)
        """
        AFFIX_KEYS = [
            "affix_gong", "affix_shang", "affix_jue", "affix_zhi", "affix_yu"
        ]
        KEY_NAMES = [tr("宫"), tr("商"), tr("角"), tr("徵"), tr("羽")]

        affixes: list[Affix] = []
        warnings: list[str] = []

        for i, (key, cn_name) in enumerate(zip(AFFIX_KEYS, KEY_NAMES, strict=False)):
            text = raw.get(key, "").strip()

            if not text:
                if i == 0:
                    warnings.append(f"词条{cn_name}({key}) 为空，OCR 完全失败")
                    break
                elif i <= 3:
                    # 第 2~4 条为空 → 级联丢弃后续
                    remaining = AFFIX_KEYS[i:]
                    warnings.append(
                        f"词条{cn_name}({key}) 为空，"
                        f"后续 {len(remaining) - 1} 条已丢弃"
                    )
                    break
                else:
                    # 第 5 条为空 → 正常
                    break

            affix = self._parse_single_affix(text)
            if affix is None:
                warnings.append(f"词条{cn_name}({key}) 无法解析: {text!r}")
                # 套装信息等非词条内容，跳过但不中断
                if tr("套装") in text:
                    continue
                break
            affixes.append(affix)

        return affixes, warnings

    def _parse_single_affix(self, raw: str) -> Affix | None:
        """解析单条词条

        处理流程：
        0. 数据清洗（委托 cleaner：误识别替换 + 噪声字符删除）
        1. 检测 [转] 转律标记
        2. 过滤套装信息
        3. 匹配已知词条名称（最长前缀优先）
        4. 提取数值和单位

        Returns:
            Affix 或 None（无法解析时）
        """
        # 输入应由 OCR 引擎清洗，此处直接使用
        text = raw.strip() if raw else ""
        if not text:
            return None

        # ── 1. 转律标记检测与移除 ──
        # 符号已统一为英文括号，匹配 [转1] / [转] / [转1 / [转 等变体
        is_transferred = bool(re.search(tr(r"\[转[1\]]?"), text))
        text = re.sub(tr(r"\[转[1\]]?"), "", text)

        # ── 2. 过滤套装信息 ──
        if tr("套装") in text:
            return None

        # ── 3. 匹配已知词条名称 ──
        matched_name = None
        remainder = text

        # 先尝试武学增伤（动态前缀）
        wuxue_m = WUXUE_PATTERN.match(text)
        if wuxue_m:
            matched_name = wuxue_m.group(0)  # 如 "剑武学增伤"
            remainder = text[wuxue_m.end():]
        else:
            # 按长度降序匹配，保证最长前缀
            for name in AFFIX_NAMES:
                if text.startswith(name):
                    matched_name = name
                    remainder = text[len(name):]
                    break

        if matched_name is None:
            return None

        # ── 4. 提取数值 ──
        # 移除空格和非中文字符（保留数字、小数点、%、+）
        clean = re.sub(r"[^\d.%+\u4e00-\u9fff]", "", remainder)
        # 再移除所有中文字符（OCR 噪声）
        clean = re.sub(r"[\u4e00-\u9fff]", "", clean)

        # 提取数字
        num_m = re.search(r"(\d+\.?\d*)", clean)
        if not num_m:
            return None

        value = float(num_m.group(1))

        # ── 5. 单位检测 ──
        unit = "%" if matched_name in PERCENT_AFFIXES else None

        return Affix(
            name=matched_name,
            value=value,
            unit=unit,
            is_transferred=is_transferred,
        )


# ─── 全局单例 ─────────────────────────────────────────────

_parser_instance: EquipmentParser | None = None


def get_equipment_parser() -> EquipmentParser:
    """获取全局 EquipmentParser 单例"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = EquipmentParser()
    return _parser_instance
