"""流派调律策略的唯一来源：界面五槽要求、普通/转律潜力和自然必需项。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from lvjiang.apps.shuijiao_tuning.core.equip_validator import duplicate_tuned_names

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.equipment_slots import SLOT_BY_KEY
from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates
from lvjiang.apps.yysls.core.equip_parser import EquipmentData
from lvjiang.apps.yysls.core.loadout.transmute import transmute_candidates

PARTS = ("main_weapon", "sub_weapon", "ring", "pendant", "head", "chest", "leg", "wrist")
PART_NAMES = ("武器一", "武器二", "环", "佩", "头甲", "胸甲", "胫甲", "腕甲")
ARMOR_KEYS = {"冠胄": "head", "胸甲": "chest", "胫甲": "leg", "腕甲": "wrist"}
QISHU = "全奇术增伤"
OTHER_SMALL = "其他流派小属攻"


def is_qishu(name: str) -> bool:
    return name in (QISHU, "奇术增伤") or get_game_config().resolve_affix_category(name) == "奇术类增伤"


def is_divine(name: str) -> bool:
    """游戏的自然神力词条；不能转律生成，不由用户目标模式决定。"""
    gc = get_game_config()
    return (name in gc.get_wuxue_affix_names() or name == "全武学增效" or is_qishu(name)
            or name in ("对首领单位增伤", "对玩家单位增效"))


def divine_target(profile: dict, part: str, weapon: str = "") -> str:
    if weapon:
        index = 0 if part == "main_weapon" else 1
        return skill_name(weapon) if profile["skills"][index] == "required" else ""
    if part in ("ring", "pendant"):
        return "全武学增效" if profile.get("all_skill", True) else ""
    if part in ("leg", "wrist"):
        return profile.get("unit", "对首领单位增伤")
    return QISHU if profile.get("qishu", False) else ""


def clean_flexible_pool(profile: dict, card: dict, modes: list[str], text: str) -> str:
    """确定要出的后四条红词不能再充当额外蓝词；首条不参加剔除。"""
    forced = {f for f, m in zip(card["fields"][1:], modes[1:], strict=True) if m in ("required", "divine") and f and "/" not in f}
    forced_qishu = any(is_qishu(t) for t in forced)
    return "/".join(t for t in normalize_field(text).split("/") if t and t not in forced
                    and (profile.get("qishu_allowed", False) and not forced_qishu or not is_qishu(t)))


def preset_totals(profile: dict) -> dict:
    from collections import Counter
    totals: Counter[str] = Counter()
    for card in cards(profile):
        for field in card["fields"]:
            if field and "/" not in field:
                totals[field] += 1
    totals["神力"] = sum(n for name, n in list(totals.items()) if is_divine(name))
    return dict(totals)


def unique_targets_possible(groups: list[set[str]], blue: set[str]) -> bool:
    def assign(index: int, used: set[str]) -> bool:
        if index == len(groups):
            return len(blue - used) >= 4 - len(groups)
        return any(assign(index + 1, used | {name}) for name in groups[index] - used)
    return assign(0, set())


def expand_profile_field(field: str, profile: dict, physical: set) -> set[str]:
    from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
        attribute_members,
    )

    values = set(field.split("/")) - {""}
    if QISHU in values:
        values = values - {QISHU} | {t for t in physical if get_game_config().resolve_affix_category(t) == "奇术类增伤"}
    if OTHER_SMALL in values:
        attr = (get_game_config().get_playstyle(profile["playstyle"]) or {}).get("attr", "")
        values = values - {OTHER_SMALL} | (set(attribute_members("最小属性攻击")) - {f"最小{attr}攻击"})
    if "对首领单位增伤" in values:
        values.add("对玩家单位增效")
    return values


def profile_candidates(profile: dict, card: dict, index: int) -> list[str]:
    """编辑器、补全、默认灵活池共用同一份候选。"""
    from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
        attribute_members,
        initial_categories,
    )

    spec = SLOT_BY_KEY[card["key"]]
    if index == 0:
        return initial_categories(spec.part)
    gc = get_game_config()
    pool = set().union(*(set(normal_affix_candidates({"type": card["weapon"] or spec.part, "level": level}, gc))
                         for level in (105, 110, 115)))
    attr = (gc.get_playstyle(profile["playstyle"]) or {}).get("attr", "")
    all_attrs = set(attribute_members("最小属性攻击") + attribute_members("最大属性攻击"))
    pool -= all_attrs - ({f"最小{attr}攻击", f"最大{attr}攻击"} if not spec.is_weapon else set())
    if not spec.is_weapon and profile["mode"] == "small":
        pool.add(OTHER_SMALL)
    if not profile.get("qishu_allowed", False):
        pool = {t for t in pool if not is_qishu(t)}
    if spec.key in ("head", "chest") and profile.get("qishu_allowed", False):
        pool = {t for t in pool if not is_qishu(t)} | {QISHU}
    return sorted(pool)


def allowed_pool(profile: dict, card: dict, physical: set, junk=(), extra_junk=None) -> tuple[set, set]:
    gc = get_game_config()
    mode = profile["mode"]
    extra_junk = extra_junk or {}
    stats = {"最大外功攻击", "最小外功攻击", "劲", "敏", "势", "会意率", "会心率", "精准率"}
    qishu = {t for t in physical if gc.resolve_affix_category(t) == "奇术类增伤"}
    if not profile.get("qishu_allowed", False):
        qishu = set()
    allowed = physical & (stats | qishu | {t for t in physical if t.endswith("攻击")})
    allowed |= {card["optional_skill"]} - {""}
    if card["key"] in ("ring", "pendant") and profile.get("all_skill", True):
        allowed.add("全武学增效")
    forbidden = (set(extra_junk.get(mode, ("最大外功攻击", "劲", "势"))) | {"会意率"} if mode == "small" else
                 set(extra_junk.get(mode, ("最小外功攻击", "敏"))) if mode == "large" else
                 {"敏"} if mode == "wei" else {"会意率", "势"})
    allowed -= forbidden
    attr = (gc.get_playstyle(profile["playstyle"]) or {}).get("attr", "")
    allowed -= {t for t in allowed if t.startswith("最大") and t.endswith("攻击")
                and t not in ("最大外功攻击", "最大无相攻击", f"最大{attr}攻击")}
    if mode == "large":
        allowed -= {"会心率", "精准率"} | {t for t in allowed if t.startswith("最小") and t.endswith("攻击")}
        if not card["weapon"]:
            allowed.discard("最大无相攻击")
    allowed -= set(junk) - {card["optional_skill"]}
    if not profile.get("all_skill", True):
        allowed.discard("全武学增效")
    return allowed, forbidden


def editable_candidates(profile: dict, card: dict, index: int) -> list[str]:
    """必需神力固定；可选奇术仍能从蓝色候选库选择。"""
    return [t for t in profile_candidates(profile, card, index)
            if index == 0 or not is_divine(t) or
            (is_qishu(t) and profile.get("qishu_allowed", False) and not profile.get("qishu", False))]


def editor_cards(profile: dict, overrides=None, field_modes=None) -> list[dict]:
    from lvjiang.apps.shuijiao_tuning.core.junk_recycle import DEFAULT_JUNK_AFFIXES

    result = cards(profile, overrides)
    for card in result:
        saved = (field_modes or {}).get(card["key"])
        card["modes"] = list(saved) if saved is not None else ["initial", *[
            "required" if value else "none" for value in card["fields"][1:]]]
        if saved is None:
            candidates = set(profile_candidates(profile, card, 1))
            pool, _ = allowed_pool(profile, card, candidates, DEFAULT_JUNK_AFFIXES)
            if profile["mode"] == "small" and not card["weapon"]:
                pool.add(OTHER_SMALL)
            for i in range(1, 5):
                if not card["fields"][i]:
                    card["fields"][i] = "/".join(sorted(pool))
                    card["modes"][i] = "flexible"
                    break
        for i in range(1, 5):
            if card.get("divine") and card["fields"][i] == card["divine"]:
                card["modes"][i] = "divine"
            elif (card["modes"][i] == "divine" and is_qishu(card["fields"][i])
                  and profile.get("qishu_allowed", False) and not profile.get("qishu", False)):
                card["modes"][i] = "flexible"
        for i in range(1, 5):
            if card["modes"][i] == "flexible":
                card["fields"][i] = clean_flexible_pool(profile, card, card["modes"], card["fields"][i])
        if (card["key"] in ("head", "chest") and profile.get("qishu_allowed") and not profile.get("qishu")
                and QISHU not in card["fields"][1:]):
            empty = next((i for i in range(1, 5) if not card["fields"][i]), None)
            if empty is not None and any(QISHU in f.split("/") and m == "flexible"
                                         for f, m in zip(card["fields"], card["modes"], strict=True)):
                for i in range(1, 5):
                    if card["modes"][i] == "flexible":
                        card["fields"][i] = "/".join(t for t in card["fields"][i].split("/") if t != QISHU)
                card["fields"][empty] = QISHU
                card["modes"][empty] = "flexible"
    return result


def apply_requested_presets(config: dict, catalog: dict | None = None) -> dict:
    """仅返回目标字段补丁；调用方锁内合并，材料和勾选项不归本函数所有。"""
    catalog = catalog or load_profiles()
    previous = config.get("profile_overrides", {})
    previous_modes = config.get("profile_field_modes", {})
    targets: dict = {}
    modes: dict = {}
    for key, profile in catalog.items():
        reset = profile["mode"] in ("small", "large")
        part_cards = editor_cards(profile, None if reset else previous.get(key), None if reset else previous_modes.get(key))
        targets[key], modes[key] = {}, {}
        for card in part_cards:
            if not profile.get("qishu_allowed", False):
                for i in range(1, 5):
                    card["fields"][i] = "/".join(t for t in card["fields"][i].split("/") if not is_qishu(t))
                    if not card["fields"][i]:
                        card["modes"][i] = "none"
            if not any(f and m == "flexible" for f, m in zip(card["fields"][1:], card["modes"][1:], strict=True)) and sum(
                    bool(f) and m == "required" for f, m in zip(card["fields"][1:], card["modes"][1:], strict=True)) < 4:
                defaults = next(c for c in editor_cards(profile) if c["key"] == card["key"])
                pool = "/".join(f for f, m in zip(defaults["fields"], defaults["modes"], strict=True) if m == "flexible")
                index = next((i for i in range(1, 5) if not card["fields"][i]), None)
                if index is not None:
                    card["fields"][index] = pool
                    card["modes"][index] = "flexible"
            for i in range(1, 5):
                if card["modes"][i] == "flexible":
                    card["fields"][i] = clean_flexible_pool(profile, card, card["modes"], card["fields"][i])
            targets[key][card["key"]] = card["fields"]
            modes[key][card["key"]] = card["modes"]
    validate_settings(list(catalog), targets, catalog, modes)
    return {"profile_overrides": targets, "profile_field_modes": modes}


def load_profiles() -> dict:
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parents[1] / "config/profile_catalog.json").read_text(encoding="utf-8"))["profiles"]


def legacy_rules(selected: list[str], catalog: dict) -> dict:
    rules: dict = {}
    for key in selected:
        profile = catalog[key]
        entry = rules.setdefault(profile["rule"], {"enabled": True, "playstyles": []})
        if profile["playstyle"] not in entry["playstyles"]:
            entry["playstyles"].append(profile["playstyle"])
    return rules


def selected_from_legacy(rules: dict, catalog: dict) -> list[str]:
    selected = []
    for key, profile in catalog.items():
        cfg = rules.get(profile["rule"], {})
        styles = cfg.get("playstyles")
        if cfg.get("enabled") and (styles is None or profile["playstyle"] in styles
                                   or key == "yu" and "飞天玉" in styles):
            selected.append(key)
    return selected


def skill_name(weapon: str) -> str:
    name = get_game_config().get_weapon_wuxue_affix(weapon)
    if not name:
        raise ValueError(f"武器「{weapon}」未配置普通武学词条")
    return name


def normalize_field(field: str) -> str:
    """接受游戏配置已有简称，不引入另一套词条别名。"""
    gc = get_game_config()
    known = set(gc.get_normal_affix_names()) | {QISHU, OTHER_SMALL, "其他"}
    names: list[str] = []
    for token in field.split("/"):
        token = token.strip()
        if not token or token == "------":
            continue
        if is_qishu(token):
            token = QISHU
        names.extend([token] if token in known else gc.get_affix_names_for_alias(token) or [token])
    return "/".join(dict.fromkeys(names))


def cards(profile: dict, overrides: dict | None = None) -> list[dict]:
    """第一框为固定首条范围，后四框为整件必需词条；空框不限定。"""
    style = get_game_config().get_playstyle(profile["playstyle"])
    if not style:
        raise ValueError(f"玩法配置不存在：{profile['playstyle']}")
    result = []
    mode = profile["mode"]
    for i, (key, label) in enumerate(zip(PARTS, PART_NAMES, strict=True)):
        weapon = str(style.get("main_weapon" if i == 0 else "sub_weapon", "")) if i < 2 else ""
        if i < 4:
            first = "最大外功攻击" if mode == "large" else "最小外功攻击" if mode == "small" else "最大外功攻击/最小外功攻击"
            required = (["最小外功攻击", "敏"] if mode == "small" else
                        ["最大外功攻击"] if mode == "large" else [])
        else:
            first = ("劲" if i >= 6 else "会意率") if mode == "large" else (
                "会心率/精准率" if mode == "small" else
                "劲/势/会意率/会心率/精准率" if mode == "wei" else "劲/敏/会心率/精准率")
            required = ["敏", "最小外功攻击"] if mode == "small" else ["最大外功攻击"] if mode == "large" else []
        if weapon and profile.get("weapon_first"):
            first = profile["weapon_first"][i]
        natural = []
        optional_skill = ""
        if weapon:
            status = profile["skills"][i]
            if status == "required":
                natural.append(skill_name(weapon))
                optional_skill = skill_name(weapon)
            elif status == "optional":
                optional_skill = skill_name(weapon)
        elif key in ("ring", "pendant") and profile.get("all_skill", True):
            natural.append("全武学增效")
        elif key in ("leg", "wrist"):
            natural.append(profile.get("unit", "对首领单位增伤"))
        elif key in ("head", "chest") and profile.get("qishu"):
            natural.append(QISHU)
        fields = [first, *required, *natural]
        if len(fields) > 5:
            raise ValueError(f"{profile['label']}的{label}要求超过五条")
        fields += [""] * (5 - len(fields))
        if overrides and key in overrides:
            fields = [normalize_field(field) for field in overrides[key]]
        divine = divine_target(profile, key, weapon)
        if divine and divine not in fields[1:]:
            # 固定神力从可选池中独立出来；不能用编辑目标绕过自然必需项。
            fields[1:] = ["/".join(t for t in f.split("/") if t != divine) for f in fields[1:]]
            empty = next((n for n in range(1, 5) if not fields[n]), None)
            if empty is None:
                raise ValueError(f"{profile['label']}·{label}须留一框给固定神力「{divine}」")
            fields[empty] = divine
        result.append({"key": key, "label": f"{label}（{weapon}）" if weapon else label,
                       "weapon": weapon, "fields": fields, "optional_skill": optional_skill, "divine": divine})
    return result


def validate_settings(selected: list, overrides: dict, catalog: dict, field_modes: dict | None = None, *,
                      strict_parts: list[str] | None = None, strict_flexible: bool = True) -> None:
    if not isinstance(selected, list) or any(key not in catalog for key in selected):
        raise ValueError("调律流派选择无效")
    if not isinstance(overrides, dict):
        raise ValueError("词条要求必须按流派、部位保存")
    known = set(get_game_config().get_normal_affix_names()) | {QISHU, OTHER_SMALL, "其他"}
    gc = get_game_config()
    for profile, parts in overrides.items():
        if profile not in catalog or not isinstance(parts, dict):
            raise ValueError("词条要求包含未知流派")
        for part, fields in parts.items():
            if part not in PARTS or not isinstance(fields, list) or len(fields) != 5:
                raise ValueError("每个部位必须恰好五个词条框")
            for field in fields:
                if not isinstance(field, str) or any(t not in known for t in normalize_field(field).split("/") if t):
                    raise ValueError(f"无效词条：{field}；多个备选词条请用 / 分隔")
                if not catalog[profile].get("qishu_allowed", False) and any(is_qishu(t) for t in normalize_field(field).split("/") if t):
                    raise ValueError(f"{catalog[profile]['label']}不使用奇术词条，请修改目标")
            card = next(c for c in cards(catalog[profile]) if c["key"] == part)
            types = {v: k for k, v in {"环": "ring", "佩": "pendant", **ARMOR_KEYS}.items()}
            equip_type = card["weapon"] or types[part]
            pool = set().union(*(set(normal_affix_candidates({"type": equip_type, "level": level}, gc))
                                 for level in (105, 110, 115)))
            for field in fields[1:]:
                names = expand_profile_field(normalize_field(field), catalog[profile], pool)
                if names and not names.intersection(pool):
                    raise ValueError(f"{catalog[profile]['label']}·{card['label']}不会产生「{field}」，请修改词条框")
    if field_modes is not None and not isinstance(field_modes, dict):
        raise ValueError("词条模式必须按流派、部位保存")
    for key, parts in (field_modes or {}).items():
        if key not in catalog or not isinstance(parts, dict):
            raise ValueError("词条模式包含未知流派")
        for part, modes in parts.items():
            relaxed = part in (strict_parts or []) and not strict_flexible
            if part not in PARTS or not isinstance(modes, list) or len(modes) != 5 or modes[0] != "initial" or any(
                    m not in ("required", "flexible", "none", "divine") for m in modes[1:]):
                raise ValueError("首条固定，后四框须选择刚需、灵活、神力或不选择")
            if key not in selected:
                continue  # An unfinished draft for another flow must not block this run.
            card = next(c for c in editor_cards(catalog[key], overrides.get(key), parts) if c["key"] == part)
            modes = card["modes"]
            if not card["fields"][0]:
                raise ValueError("首条是固定刚需，请至少选择一个首条候选")
            forced = [v for v, m in zip(card["fields"][1:], modes[1:], strict=True) if v and "/" not in v and m in ("required", "divine")]
            if duplicate_tuned_names(forced):
                raise ValueError("后四条不能把同一词条设置为两条刚需；固定首条可重复一次")
            if not relaxed and not any(v and m == "flexible" for v, m in zip(card["fields"][1:], modes[1:], strict=True)) and sum(
                    bool(v) and m in ("required", "divine") for v, m in zip(card["fields"][1:], modes[1:], strict=True)) < 4:
                raise ValueError(f"{catalog[key]['label']}·{card['label']}请补充蓝色灵活候选，或填写四个刚需")
            spec = SLOT_BY_KEY[part]
            possible = False
            for level in (105, 110, 115):
                physical = set(normal_affix_candidates({"type": card["weapon"] or spec.part, "level": level}, gc))
                groups = [expand_profile_field(f, catalog[key], physical) & physical
                          for f, m in zip(card["fields"][1:], modes[1:], strict=True) if f and m in ("required", "divine")]
                blue = set().union(*(expand_profile_field(f, catalog[key], physical) & physical
                                     for f, m in zip(card["fields"][1:], modes[1:], strict=True) if f and m == "flexible"))
                if relaxed:
                    blue = physical
                possible |= unique_targets_possible(groups, blue)
            if not possible:
                raise ValueError(f"{catalog[key]['label']}·{card['label']}候选不足以组成四个不同普通词条，请补充蓝池")


@dataclass(frozen=True)
class Assessment:
    applicable: bool
    viable: bool
    missing: str = ""


class ProfileJudge:
    def __init__(self, catalog: dict, overrides: dict | None = None, junk: tuple[str, ...] = (), *,
                 extra_junk: dict | None = None, minimum: int = 2, count_enabled: bool = True, field_modes: dict | None = None,
                 strict_parts: list[str] | None = None, strict_flexible: bool = True):
        self.catalog = deepcopy(catalog)
        self.overrides = deepcopy(overrides or {})
        self.junk = set(junk) - {"对玩家单位增效"}
        self.extra_junk = {k: [v for v in values if v != "对玩家单位增效"] for k, values in (extra_junk or {}).items()}
        self.field_modes = deepcopy(field_modes or {})
        self.minimum = minimum
        self.count_enabled = count_enabled
        self.strict_parts = set(strict_parts or [])
        self.strict_flexible = strict_flexible
        self._cards = {key: editor_cards(p, self.overrides.get(key), self.field_modes.get(key)) for key, p in self.catalog.items()}
        self._equipment_key = ""
        self._assessments: dict[str, Assessment] = {}

    def is_strict(self, equip: EquipmentData, keys: list[str]) -> bool:
        return any(card["key"] in self.strict_parts and
                   (card["weapon"] == equip.weapon if equip.weapon else SLOT_BY_KEY[card["key"]].part == equip.part)
                   for key in keys for card in self._cards[key])

    def matches_initial(self, key: str, equip: EquipmentData) -> bool:
        if not equip.affixes:
            return False
        profile = self.catalog[key]
        card = next((c for c in self._cards[key] if (
            c["weapon"] == equip.weapon if equip.weapon else SLOT_BY_KEY[c["key"]].part == equip.part)), None)
        if card is None:
            return False
        gc = get_game_config()
        physical = set(normal_affix_candidates(equip.to_dict(include_fp=False), gc))
        names = expand_profile_field(card["fields"][0], profile, physical)
        if "其他" in names:
            from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
                initial_categories,
            )
            names = (names - {"其他"}) | (set(gc.get_normal_affix_names()) - set(initial_categories(equip.part)))
        return equip.affixes[0].name in names and equip.affixes[0].name not in self.junk

    def assess(self, key: str, equip: EquipmentData) -> Assessment:
        fingerprint = repr(equip.to_dict(include_fp=False))
        if fingerprint != self._equipment_key:
            self._equipment_key = fingerprint
            self._assessments.clear()
        if key not in self._assessments:
            self._assessments[key] = self._assess(key, equip)
        return self._assessments[key]

    def transition_keys(self, equip: EquipmentData, keys: list[str]) -> list[str]:
        """并集内但尚未达到完整目标的过渡装；首条仍必须合法。"""
        result = []
        gc = get_game_config()
        physical = set(normal_affix_candidates(equip.to_dict(include_fp=False), gc))
        for key in keys:
            if (self.assess(key, equip).viable or not equip.affixes or equip.affixes[0].name in self.junk
                    or duplicate_tuned_names([a.name for a in equip.affixes[1:]])):
                continue
            for card in self._cards[key]:
                spec = SLOT_BY_KEY[card["key"]]
                if (card["weapon"] != equip.weapon if equip.weapon else spec.part != equip.part):
                    continue
                if card["key"] in self.strict_parts:
                    continue
                if len(equip.affixes) == 5 and card["divine"] and not any(
                        a.name in expand_profile_field(card["divine"], self.catalog[key], physical) for a in equip.affixes[1:]):
                    continue
                modes = card["modes"]
                first = expand_profile_field(card["fields"][0], self.catalog[key], physical)
                if "其他" in first:
                    from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
                        initial_categories,
                    )
                    first = first - {"其他"} | (set(gc.get_normal_affix_names()) - set(initial_categories(equip.part)))
                union = set().union(*(expand_profile_field(f, self.catalog[key], physical)
                                      for f, m in zip(card["fields"][1:], modes[1:], strict=True) if m != "none"))
                union |= first  # 固定首条候选也是刚需集合的一部分。
                if self.field_modes.get(key, {}).get(card["key"]) is None:
                    _, forbidden = allowed_pool(self.catalog[key], card, physical, self.junk, self.extra_junk)
                    required = set().union(*(expand_profile_field(f, self.catalog[key], physical)
                        for f, m in zip(card["fields"][1:], modes[1:], strict=True) if m in ("required", "divine")))
                    union -= (self.junk | forbidden) - required
                if not self.catalog[key].get("qishu_allowed", False):
                    union = {t for t in union if not is_qishu(t)}
                if first and equip.affixes[0].name in first and all(a.name in union for a in equip.affixes[1:]):
                    result.append(key)
        return result

    def _assess(self, key: str, equip: EquipmentData) -> Assessment:
        profile = self.catalog[key]
        profile_cards = self._cards[key]
        part = ("main_weapon" if equip.weapon == profile_cards[0]["weapon"] else
                "sub_weapon" if equip.weapon == profile_cards[1]["weapon"] else "") if equip.weapon else (
                    {"环": "ring", "佩": "pendant", **ARMOR_KEYS}.get(equip.part, ""))
        if not part:
            return Assessment(False, False)
        card = next(c for c in profile_cards if c["key"] == part)
        if not equip.affixes:
            return Assessment(True, False, "首词条未识别")
        if duplicate_tuned_names([a.name for a in equip.affixes[1:]]):
            return Assessment(True, False, "后四普通词条重复，不是合法装备组合")
        gc = get_game_config()
        data = equip.to_dict(include_fp=False)
        physical = set(normal_affix_candidates(data, gc))
        qishu = {t for t in physical if gc.resolve_affix_category(t) == "奇术类增伤"}

        def expand(field: str) -> set[str]:
            return expand_profile_field(field, profile, physical)

        first = expand(card["fields"][0])
        if "其他" in first:
            from lvjiang.apps.shuijiao_tuning.core.tuning_history.frequency_groups import (
                initial_categories,
            )

            first = (first - {"其他"}) | (set(gc.get_normal_affix_names()) - set(initial_categories(equip.part)))
        modes = card["modes"]
        groups = [expand(field) for i, field in enumerate(card["fields"][1:], 1)
                  if field and (modes is None and card["modes"][i] in ("required", "divine") or modes and modes[i] in ("required", "divine"))]
        flexible = set().union(*(expand(field) for i, field in enumerate(card["fields"][1:], 1)
                                if field and modes and modes[i] == "flexible"))
        if any(not group.intersection(physical) for group in groups):
            return Assessment(True, False, "必需词条不在该等级、部位的普通词条池中")
        names = [a.name for a in equip.affixes]
        if (first and names[0] not in first) or names[0] in self.junk:
            return Assessment(True, False, "固定首条不符合该流派")
        exclusive = {t for t in physical if "武学增" in t or t in ("对首领单位增伤", "对玩家单位增效") or t in qishu}
        natural = set().union(*groups).intersection(exclusive) if groups else set()
        required = set().union(*groups) if groups else set()
        allowed, forbidden = allowed_pool(profile, card, physical, self.junk, self.extra_junk)
        allowed |= required & physical
        if self.field_modes.get(key, {}).get(part) is None:
            flexible -= self.junk | forbidden
        if modes is not None:
            allowed = (required | flexible) & physical
        relaxed = part in self.strict_parts and not self.strict_flexible
        if relaxed:
            allowed = physical.copy()
            flexible = allowed - required
        if not relaxed and not profile.get("qishu_allowed", False):
            allowed = {t for t in allowed if not is_qishu(t)}
        bad = (self.junk | forbidden) - (required | flexible)
        if not relaxed and self.count_enabled and sum(name in bad for name in names[1:]) >= self.minimum:
            return Assessment(True, False, f"垃圾词条已达到{self.minimum}条")
        free = max(0, 5 - len(names))
        absent = ["/".join(sorted(group)) for group in groups if group <= natural and not group.intersection(names[1:])]
        if not free and absent:
            return Assessment(True, False, "缺少自然必需词条：" + "、".join(absent))

        def fits(tokens: list[str]) -> bool:
            remaining = list(tokens[1:])
            if duplicate_tuned_names(remaining) or any(t not in allowed for t in remaining):
                return False
            used = set(remaining)

            def match(index, left, budget, planned):
                if index == len(groups):
                    pool = allowed if modes is None or relaxed else flexible & allowed
                    return (modes is None or all(name in pool for name in left)) and len(pool - used - planned) >= budget
                for i, name in enumerate(left):
                    if name in groups[index] and match(index + 1, left[:i] + left[i + 1:], budget, planned):
                        return True
                return budget > 0 and any(match(index + 1, left, budget - 1, planned | {name})
                                          for name in groups[index] & allowed - used - planned)

            return match(0, remaining, free, set())

        if fits(names):
            return Assessment(True, True)
        for slot, targets in transmute_candidates(data, gc).items():
            for target in targets:
                if target in exclusive or target not in allowed:
                    continue
                changed = names.copy()
                changed[slot - 1] = target
                if fits(changed):
                    return Assessment(True, True)
        return Assessment(True, False, "至多一次合法转律仍不满足组合")

    def matches(self, keys: list[str], equip: EquipmentData) -> list[str]:
        return [self.catalog[key]["label"] for key in keys if self.assess(key, equip).viable]
