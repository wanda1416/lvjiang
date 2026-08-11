"""单元格格式化与配额周期辅助函数

提供 Profile 总览表格的单元格显示、样式、tooltip 生成，
以及配额周期（week/month）距下次重置的剩余时间计算。

所有函数均为无状态纯函数 / 静态方法，便于独立测试与复用。
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QTableWidgetItem

from ...config.profile_models import (
    DIR_BOTH,
    DIRECTION_LABELS,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StockKeyDef,
    format_sync_label,
    parse_sync_key,
)
from ...profile.profile_engine import compute_regen_entry

# ─── 配额周期辅助函数 ────────────────────────────────────────────


def quota_period_days(period: str, now: datetime | None = None) -> int:
    """配额周期对应的总天数（用于过半判断）

    month 分支使用当前月的实际天数，避免短月份 / 闰月的半程阈值偏差。
    """
    if period == "week":
        return 7
    if period == "month":
        if now is None:
            now = datetime.now()
        return calendar.monthrange(now.year, now.month)[1]
    return 0


def parse_reset_time(reset_time: str) -> tuple[int, int]:
    """解析 'HH:MM' 重置时刻；格式错误时回退 05:00"""
    try:
        parts = reset_time.split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError, AttributeError):
        return 5, 0


def quota_reset_remaining(
    kd: QuotaKeyDef, now: datetime | None = None,
) -> timedelta | None:
    """计算配额距离下一次重置的剩余时间

    week: 按 reset_day (1=周一...7=周日，0=周一) + reset_time 计算
    month: 按 reset_day (1-31，0=1 号，超出当月天数时取最后一天) + reset_time 计算
    其他周期或配置缺失时返回 None。
    """
    if now is None:
        now = datetime.now()
    reset_h, reset_m = parse_reset_time(kd.reset_time)

    if kd.period == "week":
        reset_weekday = (kd.reset_day - 1) if kd.reset_day else 0  # 0=Monday
        current_weekday = now.weekday()
        days_ahead = reset_weekday - current_weekday

        reset_today = datetime(
            now.year, now.month, now.day, reset_h, reset_m,
        )
        if days_ahead == 0 and now >= reset_today:
            days_ahead = 7
        elif days_ahead < 0:
            days_ahead += 7

        if days_ahead == 0:
            target = reset_today
        else:
            target = datetime(
                now.year, now.month, now.day, reset_h, reset_m,
            ) + timedelta(days=days_ahead)
        return target - now

    if kd.period == "month":
        reset_day = kd.reset_day if kd.reset_day else 1
        year, month = now.year, now.month
        last_day = calendar.monthrange(year, month)[1]
        actual_day = min(reset_day, last_day)

        reset_today = datetime(year, month, actual_day, reset_h, reset_m)
        if now < reset_today:
            return reset_today - now

        # 已过本月重置时刻 → 跳到下月
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        next_last_day = calendar.monthrange(next_year, next_month)[1]
        next_actual_day = min(reset_day, next_last_day)
        target = datetime(
            next_year, next_month, next_actual_day, reset_h, reset_m,
        )
        return target - now

    return None


# ─── 单元格格式化 ────────────────────────────────────────────


def format_profile_cell(kd: KeyDef, model_type: str, data: dict) -> tuple[str, str]:
    """根据模型类型格式化 profile 值用于总览显示

    返回 (display_text, style)，style 为 "" | "red_bold" | "orange_bold" | "green_bold"
    """
    entry = data.get(model_type, {}).get(kd.key, {})
    if not entry:
        return "", ""

    value = entry.get("value")
    if value is None:
        return "", ""

    if model_type == MODEL_QUOTA:
        if not isinstance(kd, QuotaKeyDef):
            return str(int(value)), ""

        # 已达上限 → 绿色（优先级最高）
        if kd.cap is not None and value >= kd.cap:
            text = f"{int(value)}/{kd.cap}" if kd.show_cap else str(int(value))
            return text, "green_bold"

        # 周/月级别：按周期进度着色（未达上限才会走到这里）
        style = ""
        if kd.period in ("week", "month") and kd.cap is not None:
            now = datetime.now()
            remaining = quota_reset_remaining(kd, now)
            if remaining is not None:
                if remaining <= timedelta(hours=48):
                    # 剩余 ≤ 48 小时且未达上限 → 红色
                    style = "red_bold"
                elif remaining <= timedelta(days=quota_period_days(kd.period, now) / 2):
                    # 周期过半且未达一半 → 橙色
                    if value < kd.cap / 2:
                        style = "orange_bold"

        text = f"{int(value)}/{kd.cap}" if kd.show_cap else str(int(value))
        return text, style

    if model_type == MODEL_REGEN:
        if isinstance(kd, RegenKeyDef):
            # 再生计算当前值；小数部分表示未展示的恢复进度。
            computed, _ = compute_regen_entry(entry, kd)
            int_value = int(computed)
            style = ""
            if kd.cap is not None and computed >= kd.cap:
                style = "red_bold"
            elif kd.alert_red is not None and computed >= kd.alert_red:
                style = "red_bold"
            elif kd.alert_orange is not None and computed >= kd.alert_orange:
                style = "orange_bold"
            if kd.show_cap and kd.cap:
                return f"{int_value}/{kd.cap}", style
            return str(int_value), style
        return str(int(value)), ""

    if model_type == MODEL_STOCK:
        if isinstance(kd, StockKeyDef) and kd.cap is not None:
            if kd.show_cap and kd.cap:
                if value >= kd.cap:
                    style = "red_bold" if not kd.soft else "orange_bold"
                    return f"{int(value)}/{kd.cap}", style
                return f"{int(value)}/{kd.cap}", ""
            # 不展示上限但达到上限时
            if value >= kd.cap:
                style = "red_bold" if not kd.soft else "orange_bold"
                return str(int(value)), style
        # 存量模型无上限时纯数字
        return str(int(value)), ""

    return str(value), ""


def is_sync_target_at_hard_cap(sync_key: str, data: dict) -> bool:
    """检查同步目标（`model_type:key` 命名空间）是否已达硬上限

    Quota/Stock：cap 非空且非 soft；Regen：cap 非空。
    命名空间缺失或与 KeyDef 实际模型不符时返回 False。
    """
    from ...config import get_profile_config

    model_type, key = parse_sync_key(sync_key)
    if not model_type:
        return False
    entry = data.get(model_type, {}).get(key, {})
    if not entry:
        return False
    value = entry.get("value", 0) or 0
    config = get_profile_config()
    kd = config.get_key(key, model_type=model_type)
    if kd is None:
        return False
    if config.get_model_type(key) != model_type:
        return False
    cap = getattr(kd, "cap", None)
    if cap is None:
        return False
    if getattr(kd, "soft", False):
        return False
    return value >= cap


def apply_cell_style(item: QTableWidgetItem, style: str) -> None:
    """应用单元格样式: '' | 'red_bold' | 'orange_bold' | 'green_bold'"""
    if style == "red_bold":
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
        item.setForeground(Qt.GlobalColor.red)
    elif style == "orange_bold":
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(255, 165, 0))  # 橙色
    elif style == "green_bold":
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(34, 139, 34))  # 森林绿


def format_cell_tooltip(kd: KeyDef, model_type: str, data: dict) -> str:
    """生成单元格悬停提示，显示元信息（更新时间等）"""
    entry = data.get(model_type, {}).get(kd.key, {})
    if not entry:
        return ""

    lines = [f"【{kd.label}】"]

    # 更新时间
    updated_at = entry.get("updated_at")
    if updated_at:
        lines.append(f"更新时间: {updated_at}")

    # 再生模型显示额外信息
    if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
        computed, new_ts = compute_regen_entry(entry, kd)
        period_labels = {"minute": "分钟", "hour": "小时", "day": "天", "week": "周"}
        period_label = period_labels.get(kd.regen_period, kd.regen_period)
        lines.append(f"回复周期: 每{period_label}")
        lines.append(f"每次回复: {kd.regen_value}")
        lines.append(f"精确值: {computed:.4f}".rstrip("0").rstrip("."))
        if new_ts and new_ts != updated_at:
            lines.append(f"已计入至: {new_ts}")
        if kd.cap is not None:
            lines.append(f"上限: {kd.cap}")

    # 配额模型显示周期、上限
    if model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef):
        period_labels = {
            "week": "每周", "month": "每月", "season": "每赛季",
            "half_season": "每半赛季", "day": "每日",
        }
        period_label = period_labels.get(kd.period, kd.period)
        if kd.cap is not None:
            lines.append(f"{period_label}上限: {kd.cap}")
        # 周/月：附加距下次重置的剩余时间
        if kd.period in ("week", "month"):
            remaining = quota_reset_remaining(kd)
            if remaining is not None:
                days = remaining.days
                hours = remaining.seconds // 3600
                lines.append(f"距重置: {days}天 {hours}小时")

    # 同步目标（所有模型通用）
    if kd.sync_targets:
        lines.append("同步到:")
        for t in kd.sync_targets:
            label = format_sync_label(t.key)
            suffix = f" [{DIRECTION_LABELS[t.direction]}]" if t.direction != DIR_BOTH else ""
            lines.append(f"  • {label} (x{t.ratio:g}){suffix}")

    return "\n".join(lines) if len(lines) > 1 else ""
