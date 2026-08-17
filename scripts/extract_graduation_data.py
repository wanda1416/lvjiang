"""Convert bundled graduation workbooks into named JSON schemes."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.apps.yysls.core.graduation.graduation_converter import (  # noqa: E402
    convert_workbook,
    validate_model,
    write_model,
)

EXCEL_DIR = ROOT / "data" / "temp" / "excel"
OUTPUT_DIR = ROOT / "config" / "system" / "yysls" / "graduation"
FILE_TO_SCHOOL = {
    "鸣金虹": "鸣金·虹", "鸣金影": "鸣金·影",
    "裂石威": "裂石·威", "裂石钧": "裂石·钧",
    "牵丝玉": "牵丝·玉", "牵丝霖": "牵丝·霖", "牵丝翊": "牵丝·翊",
    "破竹尘": "破竹·尘", "破竹风": "破竹·风",
    "破竹鸢": "破竹·鸢", "破竹樽": "破竹·樽",
}


def workbooks() -> list[tuple[Path, str]]:
    result = []
    for prefix, school in FILE_TO_SCHOOL.items():
        # 批量脚本只处理正式源文件；用户另存的“副本”应通过方案管理单独导入。
        matches = [
            path for path in EXCEL_DIR.glob(f"*{prefix}*.xlsx")
            if "副本" not in path.stem
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected one workbook for {school}, found {len(matches)}")
        result.append((matches[0], school))
    return sorted(result, key=lambda item: item[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--school")
    parser.add_argument("--scheme", default="基础方案")
    args = parser.parse_args()
    selected = [(p, s) for p, s in workbooks() if not args.school or s == args.school]
    if not selected:
        raise SystemExit(f"unknown school: {args.school}")
    for path, school in selected:
        model = convert_workbook(path, school)
        outputs = validate_model(model)
        if f"{outputs['graduation_rate'] * 100:.2f}%" != "100.00%":
            raise RuntimeError(
                f"{school} 官方满值表回算不是 100.00%："
                f"{outputs['graduation_rate'] * 100:.8f}%"
            )
        if not args.check:
            write_model(OUTPUT_DIR / f"{school}_{args.scheme}.json", model)
        print(
            f"{school}: nodes={len(model['program']['nodes'])}, "
            f"DPS={outputs['dps']:.6f}, graduation={outputs['graduation_rate']:.9f}"
        )


if __name__ == "__main__":
    main()
