# -*- coding: utf-8 -*-
"""校验 common_conditions 合并前后判定语义等价。

对比 git HEAD 旧版规则与工作区新版：逐规则/部位/档位比较
「common + pattern 合并后的条件组」多重集合（组间 OR、与顺序无关）。
预期唯一差异：huiyi_general 环.normal 新增 {劲,势} max0（用户确认）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

import yaml  # noqa: E402

from lvjiang.apps.yysls.evaluator.tuning_rules.models import TIER_KEYS  # noqa: E402
from lvjiang.apps.yysls.evaluator.tuning_rules.parsing import parse_tuning_rule  # noqa: E402

RULE_DIR = "config/system/yysls/tuning_rules"
FILES = ["heal_fire.yaml", "heal_pure.yaml", "huixin_big.yaml",
         "huixin_small.yaml", "huiyi_general.yaml"]


def load_old(fname: str) -> dict:
    raw = subprocess.run(
        ["git", "show", f"HEAD:{RULE_DIR}/{fname}"],
        capture_output=True, check=True).stdout
    return yaml.safe_load(raw.decode("utf-8"))


def load_new(fname: str) -> dict:
    with open(os.path.join(RULE_DIR, fname), encoding="utf-8") as f:
        return yaml.safe_load(f)


def merged_tiers(rule) -> dict:
    """{(部位, 档位key): 排序后的条件组 repr 列表}"""
    out = {}
    for part, pattern in rule.patterns.items():
        for tier_key in TIER_KEYS:
            groups = (getattr(rule.common, tier_key)
                      + getattr(pattern, tier_key))
            out[(part, tier_key)] = sorted(repr(g) for g in groups)
    return out


def main() -> int:
    diffs = []
    for fname in FILES:
        old = parse_tuning_rule(load_old(fname))
        new = parse_tuning_rule(load_new(fname))
        ot, nt = merged_tiers(old), merged_tiers(new)
        assert set(ot) == set(nt), f"{fname}: 部位/档位键集不一致"
        for key in sorted(ot):
            if ot[key] != nt[key]:
                diffs.append((fname, key))
                print(f"[DIFF] {fname} {key[0]}.{key[1]}")
                print(f"  old: {ot[key]}")
                print(f"  new: {nt[key]}")
    expected = [("huiyi_general.yaml", ("环", "normal_conditions"))]
    if diffs == expected:
        print("OK: 除预期的 huiyi_general 环.normal 外，其余全部等价")
        return 0
    print(f"FAIL: 差异 {diffs} != 预期 {expected}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
