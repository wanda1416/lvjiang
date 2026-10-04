"""一次性验证“无相攻击→本属攻击”规则迁移的全矩阵等价性。

本脚本及其冻结快照刻意放在 tests/ 之外，不属于常驻回归测试。只有再次调整
这次迁移涉及的动态词条语义、且确实需要与迁移前规则做全量对照时才手动运行：

    python scripts/one_off/native_attack_equivalence/verify.py
"""
from __future__ import annotations

import base64
import itertools
import json
import random
import zlib
from collections.abc import Iterator
from pathlib import Path

import yaml

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.equip_validator import validate_combination_dict
from lvjiang.apps.yysls.core.evaluator.base import JudgeResult
from lvjiang.apps.yysls.core.evaluator.rule_judge import GenericTuningJudge
from lvjiang.apps.yysls.core.loadout.transmute import (
    transmute_pool_union,
    transmute_targets,
)
from lvjiang.apps.yysls.core.tuning_rules import dynamic_affix_map, parse_tuning_rule
from lvjiang.apps.yysls.core.tuning_rules.models import expand_affix_names

ROOT = Path(__file__).parents[3]
FIXTURES = Path(__file__).parent / "fixtures"
RULES = ROOT / "config/system/yysls/tuning_rules"
KEYS = sorted(path.stem for path in FIXTURES.glob("*.yaml"))
METHODS = ("judge", "check_tuning_worthiness", "judge_with_legal_transmute")


def matrix_cases(key: str) -> Iterator[tuple[str, EquipmentData]]:
    """生成迁移当时使用的固定装备矩阵。"""
    gc = get_game_config()
    raw = yaml.safe_load((FIXTURES / f"{key}.yaml").read_text(encoding="utf-8"))
    rule = parse_tuning_rule(raw)
    rng = random.Random(20261003)
    for playstyle_name, playstyle in rule.playstyles.items():
        for level in (110, 115):
            equip_types = dict.fromkeys([
                playstyle.main.weapon,
                playstyle.sub.weapon,
                "环",
                "佩",
                "冠胄",
                "胸甲",
                "胫甲",
                "腕甲",
            ])
            for equip_type in equip_types:
                shell = {"type": equip_type, "level": level, "quality": "gold"}
                physical = normal_affix_candidates(shell, gc)
                aliases = dynamic_affix_map(playstyle.attr)
                preferred = [
                    name for name in physical
                    if name in rule.pool_set or aliases.get(name) in rule.pool_set
                ]
                group = gc.get_type_to_group()[equip_type]
                for first in gc.get_first_affixes(group, level):
                    for count, trial in itertools.product(range(1, 5), range(3)):
                        pool = preferred if trial == 0 else physical
                        if len(pool) < count:
                            continue
                        names = rng.sample(pool, count)
                        damage = (
                            playstyle.main.damage
                            if equip_type == playstyle.main.weapon
                            else playstyle.sub.damage
                            if equip_type == playstyle.sub.weapon
                            else None
                        )
                        if damage and trial == 0 and damage not in names:
                            names[-1] = damage
                        equip = EquipmentData(
                            type=equip_type,
                            name="测试装备",
                            level=level,
                            quality="gold",
                            affixes=[Affix(name=name, value=1.0) for name in [first, *names]],
                        )
                        if trial == 2:
                            equip.affixes[1].is_transferred = True
                        if validate_combination_dict(equip.to_dict(include_fp=False)):
                            continue
                        yield playstyle_name, equip


def rating_code(result: JudgeResult) -> str:
    if result.skipped:
        return "S"
    return result.rating.value[0].upper()


def verify_frozen_ratings(key: str) -> int:
    frozen = json.loads((FIXTURES / "ratings.json").read_text(encoding="utf-8"))[key]
    expected = {
        method: zlib.decompress(base64.b64decode(value)).decode()
        for method, value in frozen.items()
    }
    rule = parse_tuning_rule(
        yaml.safe_load((RULES / f"{key}.yaml").read_text(encoding="utf-8")),
    )
    cases = list(matrix_cases(key))
    if len(cases) != len(expected["judge"]):
        raise AssertionError((key, len(cases), len(expected["judge"])))
    for index, (playstyle, equip) in enumerate(cases):
        judge = GenericTuningJudge(rule, {"playstyles": [playstyle]})
        for method in METHODS:
            actual = rating_code(getattr(judge, method)(equip))
            if actual != expected[method][index]:
                raise AssertionError((
                    key,
                    playstyle,
                    method,
                    equip.to_dict(include_fp=False),
                    expected[method][index],
                    actual,
                ))
    return len(cases)


def verify_candidate_pools(key: str) -> int:
    gc = get_game_config()
    old = parse_tuning_rule(
        yaml.safe_load((FIXTURES / f"{key}.yaml").read_text(encoding="utf-8")),
    )
    new = parse_tuning_rule(
        yaml.safe_load((RULES / f"{key}.yaml").read_text(encoding="utf-8")),
    )
    if any("无相" in name for name in new.referenced_affixes()):
        raise AssertionError((key, "新规则仍引用无相词条"))
    count = 0
    for playstyle_name, equip in matrix_cases(key):
        count += 1
        aliases = dynamic_affix_map(new.playstyles[playstyle_name].attr)
        physical = normal_affix_candidates(equip.to_dict(include_fp=False), gc)
        for field in ("affix_pool", "transmute_priority"):
            old_names = expand_affix_names(getattr(old, field), physical, aliases)
            new_names = expand_affix_names(getattr(new, field), physical, aliases)
            if old_names != new_names:
                raise AssertionError((key, playstyle_name, field, old_names, new_names))
        union = transmute_pool_union(gc)
        old_pool = [
            name for name in union
            if name in old.pool_set or aliases.get(name) in old.pool_set
        ]
        new_pool = [
            name for name in union
            if name in new.pool_set or aliases.get(name) in new.pool_set
        ]
        for index in range(2, len(equip.affixes) + 1):
            data = equip.to_dict(include_fp=False)
            old_targets = transmute_targets(data, index, old_pool, gc)
            new_targets = transmute_targets(data, index, new_pool, gc)
            if old_targets != new_targets:
                raise AssertionError((key, playstyle_name, index, old_targets, new_targets))
    return count


def main() -> None:
    total = 0
    for key in KEYS:
        rating_cases = verify_frozen_ratings(key)
        pool_cases = verify_candidate_pools(key)
        total += rating_cases
        print(f"{key}: {rating_cases} 个评级用例，{pool_cases} 个候选池用例")
    print(f"一次性迁移等价性验证通过：共 {total} 组装备矩阵")


if __name__ == "__main__":
    main()
