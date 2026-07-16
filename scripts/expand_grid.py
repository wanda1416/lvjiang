"""网格区域展开脚本

从布局 JSON 中已有的少量锚点 region，按 rows×cols 网格推算出全部 region 坐标。

前提：同一行/列的 region 间距恒定（紧密排列），至少需要一个锚点即可推算，
两个同行/列锚点可自动校验步长。

用法:
    python scripts/expand_grid.py <layout_name> <scene_key> [--rows N] [--cols N]

示例:
    python scripts/expand_grid.py 默认布局 bag_item_detail --rows 5 --cols 6
    python scripts/expand_grid.py 默认布局 bag_item_detail          # 默认 5×6
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAYOUTS_DIR = PROJECT_ROOT / "config" / "local" / "layouts"


def load_layout(layout_name: str) -> dict:
    path = LAYOUTS_DIR / f"{layout_name}.json"
    if not path.exists():
        print(f"布局文件不存在: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_layout(layout_name: str, data: dict):
    path = LAYOUTS_DIR / f"{layout_name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存: {path}")


def expand_grid(
    existing: list[dict],
    rows: int,
    cols: int,
) -> list[dict]:
    """从已有锚点推算 rows×cols 网格的全部 region。

    策略：
    1. 从已有 region 中找同行（y 相同）相邻两格算 col_step；
       若只有一个，则 col_step = w_ratio。
    2. 从已有 region 中找同列（x 相同）相邻两格算 row_step；
       若只有一个，则 row_step = h_ratio。
    3. 以左上角 (min_x, min_y) 为原点展开。
    """
    if not existing:
        print("错误：没有已有锚点")
        sys.exit(1)

    # 按 (y, x) 排序，第一个即为左上角
    existing_sorted = sorted(existing, key=lambda r: (r["y_ratio"], r["x_ratio"]))
    anchor = existing_sorted[0]

    # ── 推算 col_step ──
    col_step = None
    # 找同一行（y 接近）的两个 region
    row_tolerance = anchor["h_ratio"] * 0.3
    same_row = [
        r for r in existing_sorted
        if abs(r["y_ratio"] - anchor["y_ratio"]) < row_tolerance
    ]
    same_row.sort(key=lambda r: r["x_ratio"])
    if len(same_row) >= 2:
        col_step = same_row[1]["x_ratio"] - same_row[0]["x_ratio"]
        print(f"  col_step 由锚点推算: {col_step:.10f}")
    else:
        col_step = anchor["w_ratio"]
        print(f"  col_step 取 w_ratio: {col_step:.10f}")

    # ── 推算 row_step ──
    row_step = None
    col_tolerance = anchor["w_ratio"] * 0.3
    same_col = [
        r for r in existing_sorted
        if abs(r["x_ratio"] - anchor["x_ratio"]) < col_tolerance
    ]
    same_col.sort(key=lambda r: r["y_ratio"])
    if len(same_col) >= 2:
        row_step = same_col[1]["y_ratio"] - same_col[0]["y_ratio"]
        print(f"  row_step 由锚点推算: {row_step:.10f}")
    else:
        row_step = anchor["h_ratio"]
        print(f"  row_step 取 h_ratio: {row_step:.10f}")

    # ── 以锚点为 (1,1) 原点展开 ──
    origin_x = anchor["x_ratio"]
    origin_y = anchor["y_ratio"]
    w = anchor["w_ratio"]
    h = anchor["h_ratio"]

    # 检查已有 region 的 key 命名格式
    existing_keys = {r["key"] for r in existing}
    existing_map = {(r["key"]): r for r in existing}

    results = []
    added = 0
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            key = f"bag_{row}_{col}"
            x = origin_x + (col - 1) * col_step
            y = origin_y + (row - 1) * row_step

            if key in existing_map:
                # 保留原始精确值
                results.append(existing_map[key])
            else:
                results.append({
                    "key": key,
                    "name": f"背包格{row}_{col}",
                    "x_ratio": x,
                    "y_ratio": y,
                    "w_ratio": w,
                    "h_ratio": h,
                })
                added += 1

    return results, added


def main():
    parser = argparse.ArgumentParser(description="网格区域展开")
    parser.add_argument("layout", help="布局名称（不含 .json）")
    parser.add_argument("scene", help="场景 key（如 bag_item_detail）")
    parser.add_argument("--rows", type=int, default=5, help="行数（默认 5）")
    parser.add_argument("--cols", type=int, default=6, help="列数（默认 6）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = parser.parse_args()

    data = load_layout(args.layout)
    scenes = data.get("scenes", {})
    scene_data = scenes.get(args.scene)

    if scene_data is None:
        print(f"布局中未找到场景: {args.scene}")
        sys.exit(1)

    existing = scene_data.get("regions", [])
    print(f"场景: {args.scene}")
    print(f"已有锚点: {len(existing)} 个")
    print(f"目标网格: {args.rows}行 × {args.cols}列 = {args.rows * args.cols} 个")
    print()

    new_regions, added = expand_grid(existing, args.rows, args.cols)

    print(f"\n展开结果: 新增 {added} 个，保留 {len(existing)} 个锚点")

    if args.dry_run:
        print("\n[DRY RUN] 以下 region 将被写入:")
        for r in new_regions:
            tag = "(锚点)" if any(
                e["key"] == r["key"] for e in existing
            ) else "(新增)"
            print(f"  {tag} {r['key']}: x={r['x_ratio']:.6f} y={r['y_ratio']:.6f}")
    else:
        scene_data["regions"] = new_regions
        save_layout(args.layout, data)
        print(f"完成！{args.scene} 现在共 {len(new_regions)} 个 region")


if __name__ == "__main__":
    main()
