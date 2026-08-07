"""玩家数据模型后台计算引擎

低频后台线程，每 60 秒 tick 一次，负责：
1. 周期检查与重置（daily / activity 的 period 到期清零）
2. 实时计算（realtime 按 regen_rate 回复，封顶 cap）
3. 活动进度检查（period_cap / lifetime_cap 阈值提醒）

Signals:
    alert_triggered(key, label, message): 提醒触发
    data_updated(user_name): 数据已更新，UI 可刷新
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from lvjiang.constants import USERS_DIR
from lvjiang.core.config import SessionManager, get_session_store
from lvjiang.core.user_config import UserConfigManager

from ..config.profile_models import (
    ActivityKeyDef,
    RealtimeKeyDef,
)
from ..config.user_profile import (
    get_profile_config,
    read_profile_entry,
    write_profile_entry,
)

# 提醒去重在 session.json 中的节点路径
_ALERT_HISTORY_KEY = "profile_alert_history"


# ─── 周期边界计算 ────────────────────────────────────────────


def _parse_reset_time(reset_time: str) -> tuple[int, int]:
    """解析 HH:MM 格式的重置时刻，格式错误时抛出 ValueError"""
    parts = reset_time.split(":")
    return int(parts[0]), int(parts[1])


def _get_period_boundary(period: str, reset_time: str, now: datetime) -> datetime:
    """获取当前周期的起始边界（即上一个重置时刻）

    若 now 已越过本次重置点，返回本次重置点（表示当前周期从此开始）；
    若 now 尚未到达本次重置点，返回上次重置点。
    """
    hour, minute = _parse_reset_time(reset_time)

    if period == "day":
        reset_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= reset_today:
            return reset_today
        return reset_today - timedelta(days=1)

    if period == "week":
        # Python weekday(): Monday=0 ... Sunday=6
        # 约定 reset_time 在周一 05:00
        reset_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_since_monday = now.weekday()
        this_monday = now.date() - timedelta(days=days_since_monday)
        reset_monday = datetime.combine(this_monday, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        if now >= reset_monday:
            return reset_monday
        return reset_monday - timedelta(weeks=1)

    if period == "month":
        # month 周期统一使用每月 1 日 reset_time 重置
        reset_this_month = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
        if now >= reset_this_month:
            return reset_this_month
        # 上月 1 日
        if now.month == 1:
            last_month_start = now.replace(year=now.year - 1, month=12, day=1,
                                           hour=hour, minute=minute, second=0, microsecond=0)
        else:
            last_month_start = now.replace(month=now.month - 1, day=1,
                                           hour=hour, minute=minute, second=0, microsecond=0)
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
    regen_rate: float,
    cap: int | None,
) -> int:
    """根据回复速率计算当前实时值"""
    if regen_rate <= 0 or not updated_at_str:
        return int(stored_value)

    updated_at = datetime.fromisoformat(updated_at_str)  # 格式错误直接抛异常
    elapsed_minutes = (datetime.now() - updated_at).total_seconds() / 60.0
    if elapsed_minutes <= 0:
        return int(stored_value)

    computed = stored_value + elapsed_minutes * regen_rate
    if cap is not None:
        return int(min(computed, cap))
    return int(computed)


# ─── 提醒去重 ────────────────────────────────────────────────


def _check_and_mark_alert(
    user_name: str, key: str, alert_type: str, period_key: str
) -> bool:
    """检查提醒是否已发送过（同一 key + 同一阈值 + 同一周期只提醒一次）

    返回 True 表示首次触发（需要发送），False 表示已提醒过。
    """
    store = get_session_store()
    history = store.get_node(_ALERT_HISTORY_KEY, {})
    if not isinstance(history, dict):
        history = {}

    alert_key = f"{user_name}:{key}:{alert_type}:{period_key}"
    if alert_key in history:
        return False

    history[alert_key] = datetime.now().isoformat(timespec="seconds")
    store.set_node(_ALERT_HISTORY_KEY, history)
    return True


def _clean_old_alerts(current_keys: set[str]) -> None:
    """清理已不存在的 key 的提醒记录"""
    store = get_session_store()
    history = store.get_node(_ALERT_HISTORY_KEY, {})
    if not isinstance(history, dict) or not history:
        return

    cleaned = {
        k: v for k, v in history.items()
        if k.split(":")[1] in current_keys
    }
    if len(cleaned) != len(history):
        store.set_node(_ALERT_HISTORY_KEY, cleaned)


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

                boundary = _get_period_boundary(kd.period, kd.reset_time, now)
                if not _should_reset(updated_at_str, boundary):
                    continue

                # 周期已过期，执行重置
                if model_type == "daily":
                    write_profile_entry(data, "daily", kd.key, 0)
                    logger.debug(f"[ProfileEngine] {user_name} daily.{kd.key} 周期重置")
                    modified = True

                elif model_type == "activity":
                    current_value = entry.get("value", 0)
                    current_total = entry.get("total", 0)
                    new_total = current_total + current_value
                    write_profile_entry(data, "activity", kd.key, 0, total=new_total)
                    logger.debug(
                        f"[ProfileEngine] {user_name} activity.{kd.key} "
                        f"周期重置 (total: {current_value} → {new_total})"
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

            computed = _compute_realtime_value(
                stored_value, updated_at_str, kd.regen_rate, kd.cap
            )

            # 每日补充：检测跨天时加上 regen_daily
            if kd.regen_daily > 0 and updated_at_str:
                try:
                    prev_time = datetime.fromisoformat(updated_at_str)
                    if prev_time.date() < now.date():
                        computed += kd.regen_daily
                        if kd.cap is not None:
                            computed = min(computed, kd.cap)
                except ValueError:
                    pass  # 时间戳格式错误，跳过每日补充

            if computed != stored_value:
                write_profile_entry(data, "realtime", kd.key, computed)
                modified = True

            # 检查 alert_above
            if kd.alert_above is not None and computed >= kd.alert_above:
                if _check_and_mark_alert(user_name, kd.key, "above", "current"):
                    self.alert_triggered.emit(
                        kd.key,
                        kd.label,
                        f"{kd.label} 已达 {computed}，超过阈值 {kd.alert_above}",
                    )

        # ── Step 3: 活动进度检查（activity）──
        activity_keys = config.get_keys_by_model("activity")
        for kd in activity_keys:
            if not isinstance(kd, ActivityKeyDef):
                continue
            entry = read_profile_entry(data, "activity", kd.key)
            value = entry.get("value", 0)
            total = entry.get("total", 0)

            # 周期限额检查
            if kd.period_cap > 0 and kd.alert_near_period_cap is not None:
                threshold = kd.period_cap * kd.alert_near_period_cap
                if value >= threshold:
                    period_key = f"period_{_get_period_boundary(kd.period, kd.reset_time, now).isoformat()}"
                    if _check_and_mark_alert(user_name, kd.key, "near_period_cap", period_key):
                        self.alert_triggered.emit(
                            kd.key,
                            kd.label,
                            f"{kd.label} 当期进度 {value}/{kd.period_cap}，"
                            f"已接近周期限额",
                        )

            # 总上限检查
            if kd.lifetime_cap > 0 and kd.alert_near_lifetime_cap is not None:
                threshold = kd.lifetime_cap * kd.alert_near_lifetime_cap
                if total >= threshold:
                    if _check_and_mark_alert(user_name, kd.key, "near_lifetime_cap", "total"):
                        self.alert_triggered.emit(
                            kd.key,
                            kd.label,
                            f"{kd.label} 总累积 {total}/{kd.lifetime_cap}，"
                            f"已接近账号总上限",
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
