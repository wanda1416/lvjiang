"""玩家数据模型后台计算引擎

低频后台线程，每 60 秒 tick 一次，负责：
1. 周期检查与重置（daily / activity 的 period 到期清零）
2. 实时计算（realtime 按 regen_period + regen_value 回复，封顶 cap）

Signals:
    alert_triggered(key, label, message): 提醒触发
    data_updated(user_name): 数据已更新，UI 可刷新
"""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from lvjiang.constants import USERS_DIR
from lvjiang.core.config import SessionManager
from lvjiang.core.user_config import UserConfigManager

from ..config.profile_models import RealtimeKeyDef
from ..config.profile_store import (
    get_alert_history,
    is_alert_marked,
    mark_alert,
    set_alert_history,
)
from ..config.user_profile import (
    get_profile_config,
    read_profile_entry,
    write_profile_entry,
)

# ─── 周期边界计算 ────────────────────────────────────────────


def _parse_reset_time(reset_time: str) -> tuple[int, int]:
    """解析 HH:MM 格式的重置时刻，格式错误时抛出 ValueError"""
    parts = reset_time.split(":")
    return int(parts[0]), int(parts[1])


def _get_period_boundary(
    period: str, reset_time: str, now: datetime, reset_day: int = 0
) -> datetime:
    """获取当前周期的起始边界（即上一个重置时刻）

    若 now 已越过本次重置点，返回本次重置点（表示当前周期从此开始）；
    若 now 尚未到达本次重置点，返回上次重置点。

    reset_day:
        week 周期: 1=周一 ... 7=周日（0 → 默认周一）
        month 周期: 1-31（0 → 默认 1 号）
    """
    hour, minute = _parse_reset_time(reset_time)

    if period == "day":
        reset_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= reset_today:
            return reset_today
        return reset_today - timedelta(days=1)

    if period == "week":
        # reset_day: 1=周一 ... 7=周日，0 → 默认 1（周一）
        target_wd = reset_day if 1 <= reset_day <= 7 else 1
        # Python isoweekday(): Monday=1 ... Sunday=7
        current_wd = now.isoweekday()
        days_diff = current_wd - target_wd
        reset_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        reset_date = now.date() - timedelta(days=days_diff)
        reset_dt = datetime.combine(reset_date, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        if now >= reset_dt:
            return reset_dt
        return reset_dt - timedelta(weeks=1)

    if period == "month":
        # reset_day: 1-31，0 → 默认 1
        target_day_raw = reset_day if 1 <= reset_day <= 31 else 1
        # 钳位到当月最大天数
        max_day_this = calendar.monthrange(now.year, now.month)[1]
        target_day = min(target_day_raw, max_day_this)
        reset_this_month = now.replace(
            day=target_day, hour=hour, minute=minute, second=0, microsecond=0
        )
        if now >= reset_this_month:
            return reset_this_month
        # 上月 target_day（同样钳位）
        if now.month == 1:
            prev_year, prev_month = now.year - 1, 12
        else:
            prev_year, prev_month = now.year, now.month - 1
        max_day_prev = calendar.monthrange(prev_year, prev_month)[1]
        last_month_start = now.replace(
            year=prev_year, month=prev_month,
            day=min(target_day_raw, max_day_prev),
            hour=hour, minute=minute, second=0, microsecond=0,
        )
        return last_month_start

    if period in ("season", "half_season"):
        return _get_season_boundary(period, now, hour, minute)

    raise ValueError(f"未知周期类型: {period!r}")


def _get_season_boundary(
    period: str, now: datetime, hour: int, minute: int
) -> datetime:
    """获取赛季/半赛季周期的起始边界"""
    from ..config.manager import get_game_config
    game_config = get_game_config()
    seasons = game_config.get_season_configs()
    if not seasons:
        raise ValueError("无赛季配置，无法计算赛季周期边界")

    today = now.date()
    # 找到当前所在的赛季
    for season in seasons:
        if season.start_date and season.end_date:
            if season.start_date <= today <= season.end_date:
                if period == "season":
                    return datetime.combine(
                        season.start_date,
                        datetime.min.time(),
                    ).replace(hour=hour, minute=minute)
                # half_season
                if season.first_half_end_date:
                    if today <= season.first_half_end_date:
                        return datetime.combine(
                            season.start_date,
                            datetime.min.time(),
                        ).replace(hour=hour, minute=minute)
                    return datetime.combine(
                        season.first_half_end_date + timedelta(days=1),
                        datetime.min.time(),
                    ).replace(hour=hour, minute=minute)
                # 无半赛季分割点，fallback 为整赛季
                return datetime.combine(
                    season.start_date,
                    datetime.min.time(),
                ).replace(hour=hour, minute=minute)

    raise ValueError(f"当前日期 {today} 不在任何赛季范围内")


def _should_reset(updated_at_str: str, boundary: datetime) -> bool:
    """判断 updated_at 是否在周期边界之前（即需要重置）"""
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        return updated_at < boundary
    except (ValueError, TypeError):
        return True


# ─── 实时计算 ────────────────────────────────────────────────


def _compute_realtime_value(
    stored_value: int | float,
    updated_at_str: str,
    regen_period: str,
    regen_value: float,
    cap: int | None,
    reset_time: str = "05:00",
) -> tuple[float, str]:
    """根据回复周期和回复数值计算当前实时值

    返回 (computed_value, new_updated_at)。
    - minute/hour: 按整分钟/整小时计算，小数部分不累计
    - day: 按每日重置边界计算
    """
    now = datetime.now()

    if regen_value <= 0 or not updated_at_str:
        return float(stored_value), updated_at_str

    if regen_period not in ("minute", "hour", "day"):
        logger.error(f"非法 regen_period={regen_period!r}，不计算回复")
        return float(stored_value), updated_at_str

    stored_ts = datetime.fromisoformat(updated_at_str)

    if regen_period == "day":
        days = _count_daily_regens(stored_ts, now, reset_time)
        if days <= 0:
            return float(stored_value), updated_at_str
        computed = stored_value + days * regen_value
        if cap is not None:
            computed = min(computed, cap)
        # 推进时间戳到 now，避免下次 tick 重复计入已计算的边界
        return computed, now.isoformat(timespec="seconds")

    # minute / hour
    elapsed_seconds = (now - stored_ts).total_seconds()
    if elapsed_seconds <= 0:
        return float(stored_value), updated_at_str

    elapsed_minutes = int(elapsed_seconds // 60)
    if elapsed_minutes <= 0:
        return float(stored_value), updated_at_str

    if regen_period == "hour":
        periods = elapsed_minutes // 60
    else:  # minute
        periods = elapsed_minutes

    if periods <= 0:
        return float(stored_value), updated_at_str

    computed = stored_value + periods * regen_value
    if cap is not None and computed >= cap:
        # 触顶：直接推进到 now，避免下次 tick 重复计算
        return float(cap), now.isoformat(timespec="seconds")

    # 未触顶：仅推进已计入的整周期，避免秒数误差累积
    if regen_period == "hour":
        new_ts = stored_ts + timedelta(hours=periods)
    else:
        new_ts = stored_ts + timedelta(minutes=periods)
    new_ts_str = new_ts.isoformat(timespec="seconds")

    return computed, new_ts_str


def _count_daily_regens(
    prev_time: datetime, now: datetime, reset_time: str
) -> int:
    """计算两个时间点之间经过了多少个 reset_time 重置边界

    例如 reset_time="05:00" 时，每天 05:00 是一个边界。
    prev_time 在昨天 03:00、now 在今天 10:00 → 经过 1 个边界（今天 05:00）。
    prev_time 在前天 20:00、now 在今天 10:00 → 经过 2 个边界。
    prev_time 在前天 03:00、now 在今天 03:00 → 经过 1 个边界（昨天 05:00，今天还没到）。
    """
    hour, minute = _parse_reset_time(reset_time)

    today_reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    prev_day_reset = prev_time.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 上界：now 已过今日重置点 → 计入今天；否则 → 截止到昨天
    if now >= today_reset:
        end_reset_ordinal = today_reset.toordinal()
    else:
        end_reset_ordinal = (today_reset - timedelta(days=1)).toordinal()

    # 下界：prev_time 所属的重置日（当天重置点，不论是否已过）
    start_reset_ordinal = prev_day_reset.toordinal()

    return max(end_reset_ordinal - start_reset_ordinal, 0)


# ─── 提醒去重 ────────────────────────────────────────────────


def _check_and_mark_alert(
    user_name: str, key: str, alert_type: str, period_key: str
) -> bool:
    """检查提醒是否已发送过（同一 key + 同一阈值 + 同一周期只提醒一次）

    返回 True 表示首次触发（需要发送），False 表示已提醒过。
    """
    alert_key = f"{user_name}:{key}:{alert_type}:{period_key}"
    if is_alert_marked(alert_key):
        return False

    mark_alert(alert_key, datetime.now().isoformat(timespec="seconds"))
    return True


def _clean_old_alerts(current_keys: set[str]) -> None:
    """清理已不存在的 key 的提醒记录"""
    history = get_alert_history()
    if not history:
        return

    cleaned = {
        k: v for k, v in history.items()
        if k.split(":")[1] in current_keys
    }
    if len(cleaned) != len(history):
        set_alert_history(cleaned)


# ─── ProfileEngine ───────────────────────────────────────────


class ProfileEngine(QThread):
    """后台低频计算引擎

    Signals:
        alert_triggered(key, label, message): 提醒触发
        data_updated(user_name): 数据已更新，UI 可刷新
    """

    TICK_INTERVAL = 60  # 每 60 秒 tick 一次
    CLEAN_INTERVAL = 3600  # 每 3600 秒清理一次过期提醒

    alert_triggered = pyqtSignal(str, str, str)  # (key, label, message)
    data_updated = pyqtSignal(str)  # (user_name)

    def __init__(
        self,
        user_manager: UserConfigManager,
        session_manager: SessionManager,
        parent=None,
    ):
        super().__init__(parent)
        self._user_manager = user_manager
        self._session_manager = session_manager
        self._engine_running = True
        self._last_clean = 0.0

    def request_stop(self) -> None:
        """请求引擎停止（线程安全）"""
        self._engine_running = False

    def run(self):
        """主循环：每 60 秒 tick 一次，直到引擎被 request_stop"""
        logger.info("ProfileEngine 已启动")

        while self._engine_running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"ProfileEngine tick 异常: {e}")

            # 分段睡眠，便于快速响应停止
            for _ in range(self.TICK_INTERVAL):
                if not self._engine_running:
                    break
                self.msleep(1000)

        logger.info("ProfileEngine 已停止")

    def _tick(self):
        """单次 tick：遍历所有用户执行计算"""
        config = get_profile_config()
        user_names = self._user_manager.list_users()

        for user_name in user_names:
            if not self._engine_running:
                return
            try:
                self._tick_user(user_name, config)
            except Exception as e:
                logger.warning(f"ProfileEngine 处理用户 {user_name} 失败: {e}")

        # 定期清理过期提醒
        now = time.monotonic()
        if now - self._last_clean > self.CLEAN_INTERVAL:
            all_keys = {kd.key for kd in config.get_all_keys()}
            _clean_old_alerts(all_keys)
            self._last_clean = now

    def _tick_user(self, user_name: str, config) -> None:
        """处理单个用户的一次 tick"""
        user_file = USERS_DIR / f"{user_name}.json"
        if not user_file.exists():
            return

        try:
            data = self._session_manager.load(user_name)
        except Exception as e:
            logger.warning(f"加载用户 {user_name} 失败: {e}")
            return

        now = datetime.now()
        modified = False

        # ── Step 1: 周期检查与重置（daily + activity）──
        for model_type in ("daily", "activity"):
            keys = config.get_keys_by_model(model_type)
            for kd in keys:
                entry = read_profile_entry(data, model_type, kd.key)
                updated_at_str = entry.get("updated_at", "")

                boundary = _get_period_boundary(
                    kd.period, kd.reset_time, now, getattr(kd, "reset_day", 0)
                )
                if not _should_reset(updated_at_str, boundary):
                    continue

                # 周期已过期，执行重置
                if model_type == "daily":
                    write_profile_entry(data, "daily", kd.key, 0)
                    logger.debug(f"[ProfileEngine] {user_name} daily.{kd.key} 周期重置")
                    modified = True

                elif model_type == "activity":
                    write_profile_entry(data, "activity", kd.key, 0)
                    logger.debug(
                        f"[ProfileEngine] {user_name} activity.{kd.key} 周期重置"
                    )
                    modified = True

        # ── Step 2: 实时计算（realtime）──
        realtime_keys = config.get_keys_by_model("realtime")
        for kd in realtime_keys:
            if not isinstance(kd, RealtimeKeyDef):
                continue
            entry = read_profile_entry(data, "realtime", kd.key)
            stored_value = entry.get("value", 0)
            updated_at_str = entry.get("updated_at", "")

            computed, new_ts = _compute_realtime_value(
                stored_value, updated_at_str,
                kd.regen_period, kd.regen_value, kd.cap,
                kd.reset_time,
            )

            if computed != stored_value or new_ts != updated_at_str:
                write_profile_entry(
                    data, "realtime", kd.key, computed, updated_at=new_ts
                )
                modified = True

            # 检查 alert_above
            if kd.alert_above is not None and computed >= kd.alert_above:
                if _check_and_mark_alert(user_name, kd.key, "above", "current"):
                    self.alert_triggered.emit(
                        kd.key,
                        kd.label,
                        f"{kd.label} 已达 {int(computed)}，超过阈值 {kd.alert_above}",
                    )

        # ── 写入变更 ──
        if modified:
            try:
                self._session_manager.save(user_name, data)
                self.data_updated.emit(user_name)
            except Exception as e:
                logger.error(f"保存用户 {user_name} profile 数据失败: {e}")


# ─── 引擎单例管理 ────────────────────────────────────────────

_engine: ProfileEngine | None = None


def get_or_create_engine(
    user_manager: UserConfigManager,
    session_manager: SessionManager,
) -> ProfileEngine:
    """获取或创建 ProfileEngine 单例"""
    global _engine
    if _engine is None:
        _engine = ProfileEngine(user_manager, session_manager)
    return _engine


def stop_engine() -> None:
    """停止并销毁 ProfileEngine"""
    global _engine
    if _engine is not None:
        logger.info("正在停止 ProfileEngine...")
        _engine.request_stop()
        _engine.quit()
        if not _engine.wait(5000):
            logger.warning("ProfileEngine 5 秒内未退出，强制终止")
            _engine.terminate()
            _engine.wait(2000)
        _engine = None
