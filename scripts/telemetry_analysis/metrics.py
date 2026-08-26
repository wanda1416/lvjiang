"""统计工具 + 展示用常量。平移自 ``analyze_telemetry_rolls.py``，逻辑未改。"""
from __future__ import annotations

import math

# 每格样本少于这个数就不出结论，只报计数。7 部位 × 4 材料 × 数十词条，
# 格子极多，小格必然出现极端比例，读成"发现"就是在读噪声。
MIN_CELL_N = 30

PART_LABELS = {
    "weapon": "武器", "ring": "环", "pendant": "佩", "head": "冠胄",
    "chest": "胸甲", "leg": "胫甲", "wrist": "腕甲",
}
FOOD_LABELS = {
    "none": "不加狗粮", "gold": "金色狗粮", "purple": "紫色狗粮",
    "rainbow": "彩色狗粮",
}
MODE_LABELS = {
    "normal": "普通调律", "force_tune": "强制调律",
    "tune_full_recycle": "满词条回收",
}
ROLL_BUCKETS = ((1, 1, "1"), (2, 5, "2-5"), (6, 20, "6-20"),
                (21, 50, "21-50"), (51, 10 ** 9, "51+"))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 得分区间。

    n 小、p 接近 0 时正态近似（p ± z·sqrt(p(1-p)/n)）会给出负下界或过窄
    的区间，而词条分布恰好是"几十个词条、每个几个百分点"这种场景，正是
    正态近似最不该用的地方。
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def quantiles(values: list[float], qs=(0.05, 0.25, 0.5, 0.75, 0.95)) -> dict[float, float]:
    """线性插值分位数（stdlib 的 statistics.quantiles 不接受任意分位点）。"""
    if not values:
        return {q: float("nan") for q in qs}
    s = sorted(values)
    out = {}
    for q in qs:
        pos = q * (len(s) - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        out[q] = s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return out


def roll_bucket(idx: int) -> str:
    for lo, hi, label in ROLL_BUCKETS:
        if lo <= idx <= hi:
            return label
    return "51+"
