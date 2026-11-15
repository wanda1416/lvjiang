"""Convert bundled graduation workbooks into named JSON schemes.

已归档：该脚本只扫 ``data/excel`` 顶层且默认 110 级，表按等级分目录后
已经找不到源文件。当前流程用 ``scripts/check_graduation_updates.py``
下载源表，再通过「游戏设置 → 对应流派 → 导入毕业率方案」导入模型。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.apps.yysls.core.graduation.graduation_converter import (  # noqa: E402
    convert_workbook,
    validate_model,
    write_model,
)
from lvjiang.apps.yysls.core.graduation.model_registry import (  # noqa: E402
    model_filename,
)
from lvjiang.core.paths import data_directory, get_config_system_dir  # noqa: E402

EXCEL_DIR = data_directory() / "excel"
OUTPUT_DIR = get_config_system_dir() / "yysls" / "graduation"
#: 表格作者的四舍五入与浮点误差会造成满值回算略高于/低于 100%，
#: 0.1% 以内视为正常，不再要求精确的 100.00%。
GRADUATION_RATE_TOLERANCE = 0.001
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
    parser.add_argument("--level", type=int, default=110)
    parser.add_argument("--version", type=int, default=1)
    args = parser.parse_args()
    selected = [(p, s) for p, s in workbooks() if not args.school or s == args.school]
    if not selected:
        raise SystemExit(f"unknown school: {args.school}")
    for path, school in selected:
        model = convert_workbook(
            path, school, args.scheme, args.level, args.version)
        outputs = validate_model(model)
        rate = float(outputs["graduation_rate"])
        if abs(rate - 1.0) > GRADUATION_RATE_TOLERANCE:
            raise RuntimeError(
                f"{school} 官方满值表回算毕业率 {rate * 100:.8f}%，"
                f"偏离 100% 超过允许的 {GRADUATION_RATE_TOLERANCE * 100:g}%"
            )
        if abs(rate - 1.0) > 1e-9:
            print(
                f"注意：{school} 满值回算毕业率 {rate * 100:.4f}%，"
                f"在允许的 {GRADUATION_RATE_TOLERANCE * 100:g}% 偏差内"
            )
        if not args.check:
            write_model(
                OUTPUT_DIR / model_filename(
                    school, args.scheme, args.level, args.version),
                model,
            )
        print(
            f"{school}: nodes={len(model['program']['nodes'])}, "
            f"DPS={outputs['dps']:.6f}, graduation={outputs['graduation_rate']:.9f}"
        )


if __name__ == "__main__":
    main()
