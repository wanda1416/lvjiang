"""游戏配置常量

词条归属分类、装备部位、词库类型等全局常量。
"""
from ....i18n import tr

# 承音比例
_CHENGYIN_RATIO = 0.94

# 词库类型（_pool 字段取值；缺省为普通词条）
POOL_NORMAL = "normal"
POOL_DINGYIN = "dingyin"

# 武学增效词条所在的词条类别（affix_caps 节；
# 游戏配置的武器绑定与调律规则的增伤词条候选共用）
WUXUE_CATEGORY = tr("指定武学增效")

# 词条归属分类（固定 6 类，与词组正交；定音词条不参与归属）
AFFIX_CATEGORY_NAMES = (tr("外功类"), tr("属攻类"), tr("三率类"), tr("增效类"), tr("武器类"), tr("生存类"))

# ─── equip_type → 配置 key 映射 ─────────────────────────────

_TYPE_TO_KEY = {
    # 武器类型 → weapon
    tr("陌刀"): "weapon", tr("舞绫鼓"): "weapon", tr("双刀"): "weapon",
    tr("绳镖"): "weapon", tr("横刀"): "weapon", tr("手甲"): "weapon",
    tr("剑"): "weapon", tr("枪"): "weapon", tr("扇"): "weapon", tr("伞"): "weapon",
    # 首饰
    tr("环"): "ring",
    tr("佩"): "pendant",
    # 防具
    tr("冠胄"): "head", tr("胫甲"): "leg", tr("腕甲"): "wrist",
    tr("胸甲"): "chest",
}

# 配置 key → equip_type 反向映射（仅一一对应的首饰/防具；
# 武器 key 对应多种武器类型，无法反推具体 type）
_KEY_TO_TYPE = {
    "ring": tr("环"), "pendant": tr("佩"),
    "head": tr("冠胄"), "chest": tr("胸甲"), "leg": tr("胫甲"), "wrist": tr("腕甲"),
}

# 七个装备部位（base_attrs 的全部 key，与 UI 展示顺序一致）
BASE_ATTR_PARTS = (
    "weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)

# 七个装备部位的标准中文名（词条部位候选，与 BASE_ATTR_PARTS 同序，
# 与 tuning_rules.models.QUALITY_PARTS 对齐）
EQUIP_PART_NAMES = (tr("武器"), tr("环"), tr("佩"), tr("冠胄"), tr("胸甲"), tr("胫甲"), tr("腕甲"))
