"""OCR 文本数据清洗

集中管理 OCR 文本的清洗规则：
1. 误识别替换：已知的 OCR 形近字错误 → 正确文本
2. 噪声字符删除：对解析无意义的脏字符全部删除

提供两个清洗函数：
- clean_affix_text(): 清洗词条文本（affix_* 字段）
- clean_equip_type_text(): 清洗装备类型文本（equip_type 字段）
"""

# OCR 误识别 → 正确文本（形近字，通用纠错，适用于所有字段）
OCR_REPLACEMENTS = {
    "猜准率": "精准率",   # 猜/精 误识别
    "扁武学": "扇武学",   # 扁/扇 误识别
}

# 装备类型字段专用纠错（equip_type 中的部位名 OCR 错误）
EQUIP_TYPE_REPLACEMENTS = {
    "经甲": "胫甲",       # 经/胫 形近误识别
}

# 噪声字符（如"荐"为游戏推荐标记，对解析而言是明确脏数据，全部删除）
NOISE_CHARS = ("荐",)


def clean_affix_text(raw: str) -> str:
    """清洗单条词条 OCR 文本

    先做误识别替换，再删除噪声字符，最后去除首尾空白。
    """
    text = raw
    for wrong, correct in OCR_REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    for ch in NOISE_CHARS:
        text = text.replace(ch, "")
    return text.strip()


def clean_equip_type_text(raw: str) -> str:
    """清洗装备类型 OCR 文本

    先做通用纠错，再做部位专用纠错，最后去除首尾空白。
    """
    text = raw
    for wrong, correct in OCR_REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    for wrong, correct in EQUIP_TYPE_REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    return text.strip()
