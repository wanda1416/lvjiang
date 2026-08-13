"""游戏配置常量

词条归属分类、装备部位、词库类型等全局常量。
"""

# 承音比例
_CHENGYIN_RATIO = 0.94

# 词库类型（_pool 字段取值；缺省为普通词条）
POOL_NORMAL = "normal"
POOL_DINGYIN = "dingyin"

# 武学增效词条所在的词条类别（affix_caps 节；
# 游戏配置的武器绑定与调律规则的增伤词条候选共用）
WUXUE_CATEGORY = "指定武学增效"

# 词条归属分类（固定 6 类，与词组正交；定音词条不参与归属）
AFFIX_CATEGORY_NAMES = ("外功类", "属攻类", "三率类", "增效类", "武器类", "生存类")

# ─── equip_type → 配置 key 映射 ─────────────────────────────

_TYPE_TO_KEY = {
    # 武器类型 → weapon
    "陌刀": "weapon", "舞绫鼓": "weapon", "双刀": "weapon",
    "绳镖": "weapon", "横刀": "weapon", "手甲": "weapon",
    "剑": "weapon", "枪": "weapon", "扇": "weapon", "伞": "weapon",
    # 首饰
    "环": "ring",
    "佩": "pendant",
    # 防具
    "冠胄": "head", "胫甲": "leg", "腕甲": "wrist",
    "胸甲": "chest",
}

# 配置 key → equip_type 反向映射（仅一一对应的首饰/防具；
# 武器 key 对应多种武器类型，无法反推具体 type）
_KEY_TO_TYPE = {
    "ring": "环", "pendant": "佩",
    "head": "冠胄", "chest": "胸甲", "leg": "胫甲", "wrist": "腕甲",
}

# 七个装备部位（base_attrs 的全部 key，与 UI 展示顺序一致）
BASE_ATTR_PARTS = (
    "weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)

# 七个装备部位的标准中文名（词条部位候选，与 BASE_ATTR_PARTS 同序，
# 与 tuning_rules.models.QUALITY_PARTS 对齐）
EQUIP_PART_NAMES = ("武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲")
