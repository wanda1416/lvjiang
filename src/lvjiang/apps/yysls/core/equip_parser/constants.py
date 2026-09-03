"""装备领域常量定义

部位分类、武器类型枚举、词条名称枚举等。
武器类型、词条名称均从 attributes.yaml 动态读取（模块加载时快照，
UI 新增武器后需重启方可参与识别）。
配置文件缺失或格式错误时直接抛异常，不做静默回退。
"""

# ─── 部位分类（旧，仅 parser 内部用于 base_attr 分派） ────

WEAPON_SLOTS = {"main_weapon", "sub_weapon"}
ARMOR_SLOTS = {"head", "chest", "leg", "wrist"}

# ─── 从 attributes.yaml 一次性加载全部动态配置 ────


def _load_config() -> tuple[list[str], list[str], set[str]]:
    """从 attributes.yaml 读取武器类型 + 词条别名，返回 (weapon_types, affix_names, percent_affixes)

    - weapon_types：武器类型注册表。
    - affix_names：所有普通词条别名，按长度降序。
    - percent_affixes：_unit='%' 且 _pool 非 dingyin 的全部别名。
    经 ConfigResolver 读 system←local 合并视图（用户模式 local 覆盖生效）；
    配置缺失或关键字段为空时直接抛异常。
    """
    from lvjiang.core.config import get_resolver

    rel = "yysls/game_config.yaml"
    data = get_resolver().load_merged(rel)

    # ── weapon_types ──
    raw_weapon_types = data.get("weapon_types")
    if not isinstance(raw_weapon_types, list) or not raw_weapon_types:
        raise RuntimeError(f"attributes.yaml 中 weapon_types 缺失或为空: {rel}")

    # 支持两种格式：
    # - 旧格式：纯字符串列表 ["剑", "枪", ...]
    # - 新格式：dict 列表 [{name: "剑", wuxue_affix: "剑武学增伤"}, ...]
    weapon_types = [
        str(t["name"]) if isinstance(t, dict) else str(t)
        for t in raw_weapon_types
    ]

    # ── affix_caps ──
    affix_caps = data.get("affix_caps") or {}
    if not affix_caps:
        raise RuntimeError(f"attributes.yaml 中 affix_caps 缺失或为空: {rel}")

    all_names: list[str] = []
    percent_names: list[str] = []
    for _cat, cfg in affix_caps.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("_pool") == "dingyin":
            continue
        is_percent = cfg.get("_unit") == "%"
        aliases = cfg.get("_aliases")
        cat_aliases: list[str] = []
        if isinstance(aliases, list):
            cat_aliases.extend(str(a) for a in aliases)
        elif isinstance(aliases, dict):
            for _sub, items in aliases.items():
                if isinstance(items, list):
                    cat_aliases.extend(str(a) for a in items)
        all_names.extend(cat_aliases)
        if is_percent:
            percent_names.extend(cat_aliases)

    if not all_names:
        raise RuntimeError(f"attributes.yaml 的 affix_caps 未解析到任何词条别名: {rel}")

    return (
        weapon_types,
        sorted(set(all_names), key=len, reverse=True),
        set(percent_names),
    )


WEAPON_TYPES: list[str]
AFFIX_NAMES: list[str]
PERCENT_AFFIXES: set[str]
WEAPON_TYPES, AFFIX_NAMES, PERCENT_AFFIXES = _load_config()

# ─── 装备类型分类（type-based，替代 slot-based） ────────────

WEAPON_TYPES_SET: set[str] = set(WEAPON_TYPES)
JEWELRY_TYPES_SET: set[str] = {"环", "佩"}
ARMOR_TYPES_SET: set[str] = {"冠胄", "胸甲", "胫甲", "腕甲"}


def infer_category(equip_type: str | None) -> str:
    """从装备 type 推断类别

    Returns: "weapon" / "jewelry" / "armor" / "unknown"
    """
    if equip_type in WEAPON_TYPES_SET:
        return "weapon"
    if equip_type in JEWELRY_TYPES_SET:
        return "jewelry"
    if equip_type in ARMOR_TYPES_SET:
        return "armor"
    return "unknown"


def infer_part(equip_type: str | None) -> str:
    """从装备 type 推断部位（武器统一归为「武器」，不区分主/副）

    部位与武器是两个独立维度：部位 ∈ {武器, 环, 佩, 冠胄, 胸甲,
    胫甲, 腕甲}，具体武器类型（剑/枪/扇/...）由 type 单独表达。

    Returns: "武器" / "环" / "佩" / "冠胄" / "胸甲" / "胫甲" / "腕甲" / "unknown"
    """
    if equip_type in WEAPON_TYPES_SET:
        return "武器"
    if equip_type in JEWELRY_TYPES_SET or equip_type in ARMOR_TYPES_SET:
        return equip_type
    return "unknown"

# ─── 词条名称枚举（按长度降序，保证最长前缀优先匹配）────────
# 定音池词条（_pool: dingyin）不属于普通装备词条，已排除。
# 武学增伤/增效与其他词条一样，由配置中的别名全集匹配。
# AFFIX_NAMES / PERCENT_AFFIXES 已在上方 _load_config() 中赋值

# 带 % 的词条（_unit='%' 且非定音池，已在 _load_config() 中赋值）
