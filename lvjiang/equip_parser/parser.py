"""装备 OCR 数据转换器

将 OCR 原始识别的脏数据 dict 转换为标准装备领域模型结构。
"""

import re
from loguru import logger

from .models import EquipAttr, Affix, EquipmentData
from .constants import (
    WEAPON_TYPES_SET,
    JEWELRY_TYPES_SET,
    ARMOR_TYPES_SET,
    WEAPON_TYPES,
    AFFIX_NAMES,
    WUXUE_PATTERN,
    PERCENT_AFFIXES,
    infer_category,
)


class EquipmentParser:
    """装备 OCR 数据转换器"""

    def __init__(self):
        from ..evaluator.equip_attrs import EquipAttrConfig
        self._attr_config = EquipAttrConfig()

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

        # base_attr
        if category == "weapon":
            equip.base_attr_1 = self._parse_weapon_base(
                raw.get("base_attr", "")
            )
        elif category == "jewelry":
            equip.base_attr_1 = self._parse_jewelry_base(
                raw.get("base_attr", "")
            )
        else:  # armor / unknown
            equip.base_attr_1 = self._parse_armor_base1(
                raw.get("base_attr_1", "")
            )
            equip.base_attr_2 = self._parse_armor_base2(
                raw.get("base_attr_2", "")
            )

        # 品阶推断：type + level + base_attr value → quality
        equip.quality = self._infer_quality(equip, category)

        # affixes（带级联脏数据丢弃）
        equip.affixes, affix_warnings = self._parse_affixes(raw)
        equip.warnings.extend(affix_warnings)

        # 辅助信息
        equip.extra_data["affix_count"] = len(equip.affixes)

        return equip

    def _infer_quality(self, equip: EquipmentData, category: str) -> str | None:
        """根据 type + level + base_attr 推断品阶"""
        if not equip.type or not equip.level or not equip.base_attr_1:
            return None
        value = equip.base_attr_1.value
        # 武器 value 为 [min, max]，取 max
        if isinstance(value, list):
            value = value[1] if len(value) >= 2 else value[0]
        return self._attr_config.infer_quality(equip.type, equip.level, value)

    def parse_slot(self, slot_key: str, raw: dict) -> EquipmentData:
        """向后兼容别名，新代码请用 parse()"""
        return self.parse(raw)

    def parse_all(self, raw_data: dict) -> dict[str, EquipmentData]:
        """解析所有部位

        Args:
            raw_data: 完整 OCR 数据 {key: {field: text, ...}, ...}

        Returns:
            {key: EquipmentData, ...}
        """
        return {
            key: self.parse(raw)
            for key, raw in raw_data.items()
        }

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
        raw = raw.strip()
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

        - 名称含“云珑” → 环
        - 名称含“辟邪” → 佩
        """
        if "云珑" in name:
            return "环"
        if "辟邪" in name:
            return "佩"
        return None

    def _extract_weapon_type(self, text: str) -> str | None:
        """从文本中提取武器类型或防具类别

        "武器·剑" → "剑"
        "冠胄"    → "冠胄"
        "一杆"    → None（脏数据）
        """
        # 武器格式：武器·XX
        if "武器" in text:
            for wt in WEAPON_TYPES:
                if wt in text:
                    return wt
            return None

        # 防具/首饰：直接是类别名
        armor_categories = ["冠胄", "胸甲", "胫甲", "腕甲"]
        for cat in armor_categories:
            if cat in text:
                return cat

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
        is_chengyin = "承音" in raw

        m = re.search(r"(\d+)\s*阶", raw)
        level = int(m.group(1)) if m else None

        return level, is_chengyin

    # ─── base_attr 解析 ────────────────────────────────────

    def _parse_weapon_base(self, raw: str) -> EquipAttr | None:
        """解析武器基础属性

        "外功攻击100~232"  → EquipAttr("外功攻击", [100, 232])
        "外功攻击 60~140"  → EquipAttr("外功攻击", [60, 140])
        "外功攻击 老著 52~121" → EquipAttr("外功攻击", [52, 121])  [脏]
        """
        raw = raw.strip()
        if not raw:
            return None

        # 提取范围数字（允许中间有 OCR 噪声）
        nums = re.findall(r"\d+", raw)
        if len(nums) >= 2:
            return EquipAttr(name="外功攻击", value=[int(nums[0]), int(nums[1])])

        logger.warning(f"武器 base_attr 无法解析: {raw!r}")
        return None

    def _parse_jewelry_base(self, raw: str) -> EquipAttr | None:
        """解析首饰基础属性

        "最小外功攻击 133" → EquipAttr("最小外功攻击", 133)
        "最大外功攻击 199" → EquipAttr("最大外功攻击", 199)
        """
        return self._parse_single_value_attr(raw)

    def _parse_armor_base1(self, raw: str) -> EquipAttr | None:
        """解析防具基础属性1

        "气血最大值8750"    → EquipAttr("气血最大值", 8750)
        "气血最大值 7778"   → EquipAttr("气血最大值", 7778)
        "外功攻击 花著 52~121" → 脏数据，尝试提取
        """
        return self._parse_single_value_attr(raw)

    def _parse_armor_base2(self, raw: str) -> EquipAttr | None:
        """解析防具基础属性2

        "外功防御34"     → EquipAttr("外功防御", 34)
        "外功防御 20"    → EquipAttr("外功防御", 20)
        "外功防御 生无在病27" → EquipAttr("外功防御", 27)  [脏]
        """
        return self._parse_single_value_attr(raw, expected_name="外功防御")

    def _parse_single_value_attr(
        self, raw: str, expected_name: str | None = None
    ) -> EquipAttr | None:
        """通用：解析 "名称 + 单个数字" 格式的基础属性"""
        raw = raw.strip()
        if not raw:
            return None

        # 尝试匹配已知属性名前缀
        for attr_name in ["气血最大值", "外功防御", "最大外功攻击", "最小外功攻击"]:
            if raw.startswith(attr_name):
                remainder = raw[len(attr_name):]
                nums = re.findall(r"\d+", remainder)
                if nums:
                    return EquipAttr(name=attr_name, value=int(nums[-1]))
                return None

        # 无法匹配已知名称，尝试提取末尾数字
        if expected_name:
            nums = re.findall(r"\d+", raw)
            if nums:
                return EquipAttr(name=expected_name, value=int(nums[-1]))

        logger.warning(f"base_attr 无法解析: {raw!r}")
        return None

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
        KEY_NAMES = ["宫", "商", "角", "徵", "羽"]

        affixes: list[Affix] = []
        warnings: list[str] = []

        for i, (key, cn_name) in enumerate(zip(AFFIX_KEYS, KEY_NAMES)):
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
                if "套装" in text:
                    continue
                break
            affixes.append(affix)

        return affixes, warnings

    def _parse_single_affix(self, raw: str) -> Affix | None:
        """解析单条词条

        处理流程：
        1. 检测 [转] 转律标记
        2. 过滤套装信息
        3. 匹配已知词条名称（最长前缀优先）
        4. 清洗 OCR 噪声
        5. 提取数值和单位

        Returns:
            Affix 或 None（无法解析时）
        """
        text = raw.strip()
        if not text:
            return None

        # ── 1. 转律标记 ──
        is_transferred = bool(re.search(r"[［【\[]转[\]】\]]", text))
        text = re.sub(r"[［【\[]转[\]】\]]", "", text)

        # ── 2. 过滤套装信息 ──
        if "套装" in text:
            return None

        # ── 3. 移除 OCR 噪声字符 "荐" ──
        text = text.replace("荐", "")

        # ── 4. 匹配已知词条名称 ──
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

        # ── 5. 提取数值 ──
        # 移除空格和非中文字符（保留数字、小数点、%、+）
        clean = re.sub(r"[^\d.%+\u4e00-\u9fff]", "", remainder)
        # 再移除所有中文字符（OCR 噪声）
        clean = re.sub(r"[\u4e00-\u9fff]", "", clean)

        # 提取数字
        num_m = re.search(r"(\d+\.?\d*)", clean)
        if not num_m:
            return None

        value = float(num_m.group(1))

        # ── 6. 单位检测 ──
        unit = "%" if matched_name in PERCENT_AFFIXES else None

        return Affix(
            name=matched_name,
            value=value,
            unit=unit,
            is_transferred=is_transferred,
        )
