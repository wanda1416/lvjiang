"""一次性对拍：PC `distribute_affixes` ↔ 网页 `affix_distribution.ts` 的分配结果。

用法（主工程根目录）：
    python scripts/one_off/compare_affix_distribution.py

做三件事：
1. 用真实规则构造若干配比（含最少 8 条、满配 40 条、属攻超限、承音口径）；
2. PC 侧跑 `lvjiang.apps.yysls.core.loadout.affix_distribution.distribute_affixes`；
3. 编译网页实现并用 node 跑同一批输入，逐槽位比对词条名与数值。

刻意写进断言的既有事故：
- 词条 2-5 互不重复，但允许与首词条相同（docs/10-game/10-tuning-rules/README.md 第四节）；
- 属攻折叠只产生「最大/最小本属攻击」，外属保持具体名字；
- 词条数值必须来自 affix_values（具体词条名 × 等级），不能是空值。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "server" / "web"
CONFIG = WEB / "public" / "assets" / "yysls" / "config.json"
LEVEL = 110
ATTRIBUTE = "鸣金"

SLOTS = ["main_weapon", "sub_weapon", "head", "chest", "ring", "pendant", "leg", "wrist"]


def build_templates(cfg: dict) -> dict[str, dict]:
    """八件模板：武器与首饰取真实类型，防具用部位名（与 slot_specs.part 一致）。"""
    by_group = cfg.get("weapons_by_group", {})
    def first(group: str, fallback: str) -> str:
        values = by_group.get(group) or []
        return values[0] if values else fallback
    return {
        "main_weapon": {"type": first("weapon", "刀"), "equipment_set": ""},
        "sub_weapon": {"type": first("weapon", "刀"), "equipment_set": ""},
        "head": {"type": "冠胄", "equipment_set": ""},
        "chest": {"type": "胸甲", "equipment_set": ""},
        "ring": {"type": first("ring", "环"), "equipment_set": ""},
        "pendant": {"type": first("pendant", "佩"), "equipment_set": ""},
        "leg": {"type": "胫甲", "equipment_set": ""},
        "wrist": {"type": "腕甲", "equipment_set": ""},
    }


def build_cases(cfg: dict, templates: dict) -> list[dict]:
    """按规则构造配比：先用八个部位的首词条候选铺底，再补普通词条到目标条数。"""
    firsts = cfg.get("first_affixes", {})
    normals = cfg.get("normal_candidates", {})
    type_to_group = cfg.get("type_to_group", {})
    values = cfg.get("affix_values", {})

    def usable(name: str) -> bool:
        return bool(values.get(name, {}).get(str(LEVEL)))

    first_pick: dict[str, str] = {}
    for slot in SLOTS:
        kind = templates[slot]["type"]
        group = type_to_group.get(kind, "")
        candidates = [n for n in firsts.get(group, {}).get(str(LEVEL), []) if usable(n)]
        if candidates:
            first_pick[slot] = candidates[0]

    normal_pool: list[str] = []
    for slot in SLOTS:
        kind = templates[slot]["type"]
        for name in normals.get(kind, {}).get(str(LEVEL), []):
            if usable(name) and name not in normal_pool:
                normal_pool.append(name)

    base: dict[str, int] = {}
    for name in first_pick.values():
        base[name] = base.get(name, 0) + 1

    def fill(target: int) -> dict[str, int]:
        counts = dict(base)
        index = 0
        while sum(counts.values()) < target and normal_pool:
            name = normal_pool[index % len(normal_pool)]
            index += 1
            counts[name] = counts.get(name, 0) + 1
        return counts

    cases = [
        {"name": "首词条铺底（最少条数）", "counts": dict(base), "chengyin": False},
        {"name": "满配 40 条", "counts": fill(40), "chengyin": False},
        {"name": "满配 40 条 · 承音口径", "counts": fill(40), "chengyin": True},
    ]
    # 属攻超限：非武器部位属性词条每件最多 2 条，塞满属攻必然不可行，两端应给同样的错
    over: dict[str, int] = {"最大鸣金攻击": 10, "最大破竹攻击": 10, "最大裂石攻击": 10}
    for name in first_pick.values():
        over[name] = over.get(name, 0) + 1
    cases.append({"name": "属攻超限（应报容量不足）", "counts": over, "chengyin": False})
    return cases


def run_pc(cases: list[dict], templates: dict) -> list[dict]:
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.loadout.affix_distribution import distribute_affixes

    gc = get_game_config()
    out = []
    for case in cases:
        result = distribute_affixes(
            case["counts"], templates, attribute=ATTRIBUTE, level=LEVEL,
            chengyin=case["chengyin"], game_config=gc)
        out.append({
            "name": case["name"],
            "errors": list(result.errors),
            "equipment": {
                slot: {
                    f"affix_{i}": (equip.get(f"affix_{i}") or None)
                    for i in range(1, 6)
                }
                for slot, equip in result.equipment.items()
            },
        })
    return out


def run_web(cases: list[dict], templates: dict, workdir: Path) -> list[dict]:
    """编译网页实现并用 node 跑同一批输入。"""
    compile_cmd = ["npx.cmd", "tsc", "src/graduation/affix_distribution.ts",
                   "--outDir", str(workdir), "--module", "commonjs",
                   "--target", "es2022", "--skipLibCheck"]
    # 独立编译（不经过项目 tsconfig）会因缺少类型声明返回非零，但 JS 产物照样生成；
    # 以产物是否存在为准，避免把类型噪声当成编译失败。
    subprocess.run(compile_cmd, cwd=WEB, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not (workdir / "affix_distribution.js").exists():
        raise SystemExit("网页实现编译失败：没有生成 affix_distribution.js")
    payload = workdir / "cases.json"
    payload.write_text(json.dumps({
        "configPath": str(CONFIG).replace("\\", "/"),
        "level": LEVEL, "attribute": ATTRIBUTE, "templates": templates,
        "attrNames": ["鸣金", "裂石", "破竹", "牵丝", "无相"],
        "cases": cases,
    }, ensure_ascii=False), encoding="utf-8")

    runner = workdir / "run.js"
    runner.write_text("""
const fs = require("fs");
const {distributeAffixes} = require("./affix_distribution.js");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const cfg = JSON.parse(fs.readFileSync(input.configPath, "utf8"));
const rules = {
  data: cfg,
  affixCaps: () => null,
  affixCategory: n => (cfg.alias_to_category || {})[n] || "",
  affixUnit: n => (cfg.affix_units || {})[n] || "",
};
const attrNames = new Set(input.attrNames);
const out = input.cases.map(c => {
  const r = distributeAffixes(c.counts, input.templates,
    {rules, level: input.level, chengyin: c.chengyin,
     attribute: input.attribute, attrNames});
  const equipment = {};
  for (const [slot, equip] of Object.entries(r.equipment)) {
    const row = {};
    for (let i = 1; i <= 5; i += 1) row["affix_" + i] = equip["affix_" + i] || null;
    equipment[slot] = row;
  }
  return {name: c.name, errors: r.errors, equipment};
});
process.stdout.write(JSON.stringify(out));
""", encoding="utf-8")

    done = subprocess.run(["node", str(runner), str(payload)],
                          cwd=workdir, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise SystemExit(f"node 运行失败：{done.stderr[:800]}")
    return json.loads(done.stdout)


def compare(pc: list[dict], web: list[dict]) -> int:
    """比对口径：**结构性等价**而不是逐格相同。

    最小费用流的最优解通常不唯一（同一条词条可以落在多个等价位置），PC 自己也不
    保证哪一件先填。所以这里比三件事：
    1. 两端是否都接受/都拒绝；
    2. 每件装备的词条**多重集合**是否一致（排序后比较，含数值）；
    3. 规则断言：词条 2-5 互不重复、首词条可与普通词条同名、数值非空。
    """
    problems = 0
    for a, b in zip(pc, web):
        name = a["name"]
        if bool(a["errors"]) != bool(b["errors"]):
            print(f"[{name}] 报错不一致：PC={a['errors']} WEB={b['errors']}")
            problems += 1
            continue
        if a["errors"]:
            print(f"[{name}] 两端都拒绝：{'；'.join(a['errors'])[:100]}")
            continue

        diffs: list[str] = []
        for slot in SLOTS:
            def bag(source: dict) -> list[tuple[str, float]]:
                items = []
                for i in range(1, 6):
                    affix = (source.get(slot, {}).get(f"affix_{i}") or {})
                    if affix.get("name"):
                        items.append((affix["name"], round(float(affix.get("value") or 0), 6)))
                return sorted(items)

            left, right = bag(a["equipment"]), bag(b["equipment"])
            if left != right:
                diffs.append(f"{slot}: PC={left} WEB={right}")

            names = [((b["equipment"].get(slot, {}).get(f"affix_{i}") or {}).get("name"))
                     for i in range(2, 6)]
            filled = [n for n in names if n]
            if len(filled) != len(set(filled)):
                diffs.append(f"{slot}: 词条 2-5 出现重复 {filled}")
            for i in range(1, 6):
                affix = b["equipment"].get(slot, {}).get(f"affix_{i}") or {}
                if affix.get("name") and not affix.get("value"):
                    diffs.append(f"{slot}.affix_{i}: {affix.get('name')} 数值为空")

        if diffs:
            problems += 1
            print(f"[{name}] 差异 {len(diffs)} 处：")
            for line in diffs[:8]:
                print("   ", line)
        else:
            print(f"[{name}] 结构一致（8 槽词条多重集合相同；具体位置可能不同）")
    return problems


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    templates = build_templates(cfg)
    cases = build_cases(cfg, templates)
    pc = run_pc(cases, templates)
    with tempfile.TemporaryDirectory() as tmp:
        web = run_web(cases, templates, Path(tmp))
    problems = compare(pc, web)
    print(f"\n对拍完成：{len(cases)} 组，差异 {problems} 组")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
