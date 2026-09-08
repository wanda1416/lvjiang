#!/usr/bin/env python
"""把心法的条目骨架并入 config/system/yysls/attr_model/。

只增不改：已存在的条目原样保留，只补进缺失的行。所以游戏出新心法时
重跑一次即可，已经填好的数值不会被覆盖。也可以直接在「属性配置 →
心法」里按「+ 心法」新增，那边一次也是建满六重。

武学不在此列：名册以 game_config.yaml 的 martial_arts 为准，加载时
自动补齐，不需要也不应该在这里生成一份。

条目本身不带数值——数值由使用者在「游戏配置 → 属性来源」里填。脚本
的价值是把几百行的名字和重数先摆好，让补数据只剩「选词条 / 填数字 /
标无贡献」。

用法::

    .venv/bin/python scripts/gen_attr_model_entries.py            # 并入
    .venv/bin/python scripts/gen_attr_model_entries.py --dry-run  # 只看会加什么
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "config" / "system" / "yysls" / "attr_model"

#: 心法重数。游戏里固定六重，不随等级变化。
TIERS = ("一重", "二重", "三重", "四重", "五重", "六重")

#: 心法名。取自社区整理的心法表，按流派分组便于核对；国际服尚未放出的
#: 流派（裂石·钧 / 牵丝·翊 / 破竹·尘 / 破竹·鸢 / 破竹·樽）不在其中，
#: 需要在 UI 里自行新增。
INNER_WAYS: dict[str, tuple[str, ...]] = {
    "通用": (
        "易水歌", "泣血婆娑", "生龙活虎", "长生无相", "苦四时", "四时无常",
        "山月无影", "抗造大法", "归燕经", "晚雪间", "铁身决", "征人归",
        "御风之翼",
    ),
    "鸣金·虹": ("威猛歌", "千山法", "无名心法", "燎原星火"),
    "鸣金·影": ("移经易武", "凝神章", "剑气纵横", "逐狼心经"),
    "牵丝·霖": ("极乐", "杏花不见", "指玄篇注", "君臣药"),
    "牵丝·玉": ("花上月令", "葫芦飞飞", "纵地摘星", "春雷篇"),
    "破竹·风": ("忘川绝响", "断石之构", "所恨年年", "复仇"),
    "裂石·威": ("持其不攻", "山河绝韵", "磐石诀", "困兽心经"),
}


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merge(path: Path, kind: str, wanted: list[str], *, dry_run: bool) -> int:
    data = _load(path)
    if data.get("kind") not in (None, kind):
        raise SystemExit(f"{path.name} 的 kind 是 {data.get('kind')}，期望 {kind}")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    added = [name for name in wanted if name not in entries]
    if not added:
        return 0
    if dry_run:
        print(f"  {path.name}: 将新增 {len(added)} 条，例如 {added[:3]}")
        return len(added)
    for name in added:
        entries[name] = {"modeled": False}
    data["kind"] = kind
    data["entries"] = entries
    header = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    ) if path.exists() else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)
    path.write_text(f"{header}\n{body}" if header else body, encoding="utf-8")
    print(f"  {path.name}: 新增 {len(added)} 条")
    return len(added)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = parser.parse_args()

    inner_way_ids = [
        f"{name}·{tier}"
        for names in INNER_WAYS.values()
        for name in names
        for tier in TIERS
    ]
    print(f"目标目录：{TARGET_DIR}")
    total = _merge(
        TARGET_DIR / "inner_way.yaml", "inner_way", inner_way_ids,
        dry_run=args.dry_run,
    )
    if not total:
        print("没有缺失条目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
