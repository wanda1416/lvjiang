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

from .....i18n import tr
from ...config.profile_models import (
    DIR_BOTH,
    DIRECTION_LABELS,
    MODEL_NOTE,
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
from ...core.profile_engine.regen_math import (
    compute_regen_entry,
    format_seconds,
    is_realtime_regen,
    next_boundary_after,
    next_realtime_point_seconds,
)

# ─── 配额周期辅助函数 ────────────────────────────────────────────


def _format_number(value) -> str:
    """格式化 tooltip 中的数值，保留必要小数。"""
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


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
            computed = compute_regen_entry(entry, kd).value
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

    if model_type == MODEL_NOTE:
        text = entry.get("value_text", "")
        if text:
            return text, ""
        # 兼容：如果 value_text 为空但有数值 value，显示数值
        return (str(int(value)) if value else "", "")

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

    updated_at = entry.get("updated_at")
    updated_time = entry.get("updated_time")

    # 再生模型显示额外信息
    if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
        result = compute_regen_entry(entry, kd)
        stored_value = entry.get("value", 0) or 0
        period_labels = {"minute": tr("分钟"), "hour": tr("小时"), "day": tr("天"), "week": tr("周")}
        rate_label = period_labels.get(kd.regen_rate_unit, kd.regen_rate_unit)
        period_label = period_labels.get(kd.regen_period, kd.regen_period)

        if is_realtime_regen(kd):
            if updated_at:
                lines.append(f"更新时间: {updated_at}")
            if updated_time:
                lines.append(f"写入时间: {updated_time}")
            lines.append(tr("恢复类型: 实时恢复"))
            lines.append(f"恢复速率: {kd.regen_rate_value}/{rate_label}")
            seconds = next_realtime_point_seconds(entry, kd)
            lines.append(
                tr("下一点恢复: 已达上限") if seconds is None else f"下一点恢复: {format_seconds(seconds)}"
            )
            lines.append(f"存储值: {_format_number(stored_value)}")
            if kd.cap is not None:
                lines.append(f"上限: {kd.cap}")
        else:
            if updated_at:
                lines.append(f"更新时间: {updated_at}")
            if updated_time:
                lines.append(f"写入时间: {updated_time}")
            lines.append(tr("恢复类型: 准点恢复"))
            lines.append(f"恢复周期: 每{period_label}")
            lines.append(f"每次恢复: {kd.regen_amount}")
            try:
                next_ts = next_boundary_after(datetime.now(), kd)
                lines.append(f"下次恢复: {next_ts.isoformat(timespec='seconds')}")
            except (ValueError, TypeError):
                pass
            if result.updated_at and result.updated_at != updated_at:
                lines.append(f"计算至: {result.updated_at}")
            if kd.cap is not None:
                lines.append(f"上限: {kd.cap}")

    if model_type != MODEL_REGEN and updated_at:
        lines.append(f"更新时间: {updated_at}")
    if model_type != MODEL_REGEN and updated_time:
        lines.append(f"写入时间: {updated_time}")

    # note 模型显示文本值
    if model_type == MODEL_NOTE:
        text = entry.get("value_text", "")
        if text:
            lines.append(f"备注值: {text}")

    # 配额模型显示周期、上限
    if model_type == MODEL_QUOTA and isinstance(kd, QuotaKeyDef):
        period_labels = {
            "week": tr("每周"), "month": tr("每月"), "season": tr("每赛季"),
            "half_season": tr("每半赛季"), "day": tr("每日"),
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

    # 精确值显示（decimal 类型且值含小数部分）
    if kd.decimal:
        if model_type == MODEL_REGEN and isinstance(kd, RegenKeyDef):
            exact = compute_regen_entry(entry, kd).value
        else:
            exact = entry.get("value", 0) or 0
        if abs(exact - int(exact)) > 1e-9:
            lines.append(f"精确值: {exact:.4f}".rstrip("0").rstrip("."))

    # 同步目标（所有模型通用）
    if kd.sync_targets:
        lines.append(tr("同步到:"))
        for t in kd.sync_targets:
            label = format_sync_label(t.key)
            suffix = f" [{DIRECTION_LABELS[t.direction]}]" if t.direction != DIR_BOTH else ""
            lines.append(f"  • {label} (x{t.ratio:g}){suffix}")

    return "\n".join(lines) if len(lines) > 1 else ""
