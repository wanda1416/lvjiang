"""词条 OCR 文本数据清洗

集中管理词条文本的清洗规则，供 EquipmentParser 在词条匹配前委托调用：
1. 误识别替换：已知的 OCR 形近字错误 → 正确文本
2. 噪声字符删除：对解析无意义的脏字符全部删除
"""

# OCR 误识别 → 正确文本（形近字，在词条匹配前修正）
OCR_REPLACEMENTS = {
    "猜准率": "精准率",   # 猜/精 误识别
    "扁武学": "扇武学",   # 扁/扇 误识别
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
