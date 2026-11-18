"""用户 Profile 周期处理服务（不依赖 Qt）

调用方调度 tick，负责：
1. 配额条目存在性与「周期派生值变了」的 UI 刷新信号
   （quota 的周期归零是读时派生，这里**不写库**）
2. 再生计算（regen 显式区分实时恢复与准点恢复，封顶 cap）与阈值告警

通知回调由调用方提供；数据处理与桌面线程、信号和调度分离。

周期派生值的口径只有一处：读取端与显示端一律走
:func:`lvjiang.core.profile.periods.quota_current_value`，真实写入时才由
``service._materialize_period_reset`` 固化成 ``reset`` 行。因此「周一 05:00
归零」不依赖本引擎是否在跑，也不产生任何后台写入——见
``docs/30-design/02-decisions/profile/08-profile-sync.md``。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta

from loguru import logger

from lvjiang.core.config.interface import add_alert
from lvjiang.core.users.document import UserConfigManager

from ...i18n import tr
from .maintenance import profile_operation
from .models import RegenKeyDef
from .periods import (  # noqa: F401  # 测试仍从本模块引用周期边界
    get_period_boundary as _get_period_boundary,
)
from .periods import (
    next_period_boundary,
    quota_current_value,
)
from .periods import (
    parse_reset_time as _parse_reset_time,
)
from .regen import compute_regen_entry
from .repository import db_read_index, db_upsert
from .rows import Entry
from .schema import (
    get_profile_config,
)
from .store import (
    get_alert_history,
    is_alert_marked,
    mark_alert,
    set_alert_history,
    unmark_alert,
)

# ─── 周期边界计算 ────────────────────────────────────────────


def seconds_to_next_quota_boundary(config, now: datetime | None = None) -> float | None:
    """最近的 quota 周期边界还有多少秒；没有 quota 定义或周期不可解析时返回 None。

    调度方用它把 tick 对齐到边界：归零是读时派生的，只有「正好在 05:00:00
    之后刷新一次界面」才能让总览看上去当场归零，而不是最多晚一个 tick 周期。
    """
    now = now or datetime.now()
    delays: list[float] = []
    for kd in config.get_keys_by_model("quota"):
        upcoming = next_period_boundary(
            kd.period, kd.reset_time, now, getattr(kd, "reset_day", 0),
        )
        if upcoming is not None:
            delays.append((upcoming - now).total_seconds())
    return min(delays) if delays else None


# ─── 实时计算 ────────────────────────────────────────────────


def _compute_regen_value(
    stored_value: int | float,
    updated_at_str: str,
    regen_period: str,
    regen_value: float,
    cap: int | None,
    reset_time: str = "05:00",
    reset_day: int = 0,
) -> tuple[float, str]:
    """根据回复周期和回复数值计算当前实时值

    返回 (computed_value, new_updated_at)。
    - minute/hour: 按整分钟/整小时计算，小数部分不累计
    - day: 按每日重置边界计算
    - week: 按每周重置边界计算（reset_day 指定周几）
    """
    now = datetime.now()

    if regen_value <= 0 or not updated_at_str:
        return float(stored_value), updated_at_str

    if regen_period not in ("minute", "hour", "day", "week"):
        logger.error(f"非法 regen_period={regen_period!r}，不计算回复")
        return float(stored_value), updated_at_str

    stored_ts = datetime.fromisoformat(updated_at_str)

    if regen_period == "day":
        days = _count_quota_regens(stored_ts, now, reset_time)
        if days <= 0:
            return float(stored_value), updated_at_str
        computed = stored_value + days * regen_value
        if cap is not None:
            computed = min(computed, cap)
        # 推进时间戳到 now，避免下次 tick 重复计入已计算的边界
        return computed, now.isoformat(timespec="seconds")

    if regen_period == "week":
        weeks = _count_weekly_regens(stored_ts, now, reset_time, reset_day)
        if weeks <= 0:
            return float(stored_value), updated_at_str
        computed = stored_value + weeks * regen_value
        if cap is not None:
            computed = min(computed, cap)
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


def _count_quota_regens(
    prev_time: datetime, now: datetime, reset_time: str
) -> int:
    """计算两个时间点之间经过了多少个 reset_time 重置边界

    例如 reset_time="05:00" 时，每天 05:00 是一个边界。
    prev_time 在昨天 03:00、now 在今天 10:00 → 经过 2 个边界（昨天和今天 05:00）。
    prev_time 在前天 20:00、now 在今天 10:00 → 经过 2 个边界。
    prev_time 在前天 03:00、now 在今天 03:00 → 经过 2 个边界（前天和昨天 05:00）。
    """
    hour, minute = _parse_reset_time(reset_time)

    today_reset = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    prev_day_reset = prev_time.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 上界：now 已过今日重置点 → 计入今天；否则 → 截止到昨天
    if now >= today_reset:
        end_reset_ordinal = today_reset.toordinal()
    else:
        end_reset_ordinal = (today_reset - timedelta(days=1)).toordinal()

    # 下界：prev_time 之前最近一次重置点。
    # 例如 prev=04:40、now=05:09 时，今天 05:00 必须计入一次；
    # 因此 prev 尚未越过当天重置点时，下界应回退到前一天。
    if prev_time >= prev_day_reset:
        start_reset_ordinal = prev_day_reset.toordinal()
    else:
        start_reset_ordinal = (prev_day_reset - timedelta(days=1)).toordinal()

    return max(end_reset_ordinal - start_reset_ordinal, 0)


def _count_weekly_regens(
    prev_time: datetime, now: datetime, reset_time: str, reset_day: int = 0
) -> int:
    """计算两个时间点之间经过了多少个每周重置边界

    reset_day: 1=周一 ... 7=周日（0 或未设置 → 默认周一）
    例如 reset_day=1, reset_time="05:00" 时，每周一 05:00 是一个边界。
    """
    if reset_day < 1 or reset_day > 7:
        reset_day = 1  # 默认周一

    hour, minute = _parse_reset_time(reset_time)

    def get_week_reset_boundary(dt: datetime) -> datetime:
        """获取 dt 所在周的重置边界（reset_day 那天的 reset_time）"""
        # isoweekday: 1=周一 ... 7=周日
        current_weekday = dt.isoweekday()
        days_diff = reset_day - current_weekday
        reset_dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        reset_dt = reset_dt + timedelta(days=days_diff)
        return reset_dt

    # 上界：now 已过本周重置点 → 计入本周；否则 → 截止到上周
    this_week_reset = get_week_reset_boundary(now)
    if now >= this_week_reset:
        end_reset_boundary = this_week_reset
    else:
        end_reset_boundary = this_week_reset - timedelta(weeks=1)

    # 下界：prev_time 之前最近一次重置点
    prev_week_reset = get_week_reset_boundary(prev_time)
    if prev_time >= prev_week_reset:
        start_reset_boundary = prev_week_reset
    else:
        start_reset_boundary = prev_week_reset - timedelta(weeks=1)

    # 计算经过了多少周
    weeks_diff = (end_reset_boundary - start_reset_boundary).days // 7
    return max(weeks_diff, 0)


# ─── 提醒去重 ────────────────────────────────────────────────


def _clean_old_alerts(current_keys: set[str]) -> None:
    """清理已不存在的 key 的提醒记录。"""
    history = get_alert_history()
    if not history:
        return

    cleaned = {
        k: v for k, v in history.items()
        if k.split(":")[1] in current_keys
    }
    if len(cleaned) != len(history):
        set_alert_history(cleaned)


class ProfileProcessor:
    """周期处理服务；调用方负责调度、取消和 UI 通知。"""
    CLEAN_INTERVAL = 3600

    def __init__(self, user_manager: UserConfigManager, *,
                 should_stop: Callable[[], bool] = lambda: False,
                 notify_alert: Callable[[str, str, str], None] = lambda *_: None,
                 notify_updated: Callable[[str], None] = lambda _: None):
        self._user_manager = user_manager
        self._should_stop = should_stop
        self._notify_alert = notify_alert
        self._notify_updated = notify_updated
        self._last_clean = 0.0
        #: {user: {key: 派生值}}，只用于判断「显示值变了」要不要通知界面。
        self._quota_display: dict[str, dict[str, float | None]] = {}

    @profile_operation
    def tick(self):
        """单次 tick：遍历所有用户执行计算"""
        config = get_profile_config()
        user_names = self._user_manager.list_users()

        for user_name in user_names:
            if self._should_stop():
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

    def seconds_to_next_boundary(self) -> float | None:
        """最近的 quota 周期边界还有多少秒（供调度方对齐 tick）。"""
        try:
            return seconds_to_next_quota_boundary(get_profile_config())
        except Exception as error:  # noqa: BLE001 - 调度建议失败不能打断 tick
            logger.warning(f"ProfileEngine 计算下一个周期边界失败: {error}")
            return None

    def _tick_user(self, user_name: str, config) -> None:
        """处理单个用户的一次 tick

        从 DB 读取 profile 数据，逐条计算。**这里不写 quota 的库**：
        周期归零是读时派生的（:func:`~.periods.quota_current_value`），
        真实写入时才由 ``service._materialize_period_reset`` 固化成 reset 行。
        引擎只负责两件事：条目不存在时建 0 值（从 0 起步，不记账不同步），
        以及派生值变了就通知界面重画——周一 00:00 不再有任何后台写入，
        也不会有多端各自刷出一批 reset 行。
        """
        profile_data = db_read_index(user_name)
        if not profile_data:
            return

        # ── Step 1: quota 存在性与显示值变化（纯读，只有「建 0 值」这一处写入）──
        display: dict[str, float | None] = {}
        for kd in config.get_keys_by_model("quota"):
            entry = (profile_data.get("quota") or {}).get(kd.key)
            if entry is not None and entry.locked:
                # 锁定 key 物理失效：既不首次建值，也不参与显示
                display[kd.key] = None
                continue
            if entry is None:
                # 首次初始化：建 0 值条目。op_type 留空 = 不记账、不同步，
                # 它不是业务写入，只是「这个 key 从 0 起步」的物化。
                db_upsert(user_name, "quota", kd.key, 0)
                display[kd.key] = 0.0
                logger.debug(f"[ProfileEngine] {user_name} quota.{kd.key} 首次初始化")
                continue
            display[kd.key] = quota_current_value(entry, kd)

        # ── Step 2: 再生计算（regen）──
        # 只算不写：库里只存「上次真实写入的锚点」，边界/速率推算在读取端现算，
        # 固化只发生在真实写入的事务里（service 两行模式）。因此这里既不依赖
        # 同步状态，也不受只读实例影响——告警是纯读，必须照常评估。
        regen_keys = config.get_keys_by_model("regen")
        for kd in regen_keys:
            if not isinstance(kd, RegenKeyDef):
                continue
            entry = (profile_data.get("regen") or {}).get(kd.key)
            if entry is not None and entry.locked:
                # 锁定 key 既不自动恢复，也不产生阈值告警
                continue
            if entry is None:
                # 首次：以 0 值、空时间戳构造虚拟 entry，走正常计算路径
                entry = Entry(username=user_name, type="regen", key=kd.key, value=0)
            # 所有 regen 类型都读时计算，不落库：库里只存「上次真实写入的锚点」，
            # 边界/速率推算在读取端（UI、DSL、profile_read、web）现算。
            # 固化只发生在真实写入的事务里（service 两行模式），同步面上
            # 不存在任何基于本地基线的自动写入。
            regen_result = compute_regen_entry(entry, kd)
            computed = regen_result.value

            # 检查双阈值告警（用最新计算值，不论是否写入成功）
            # 橙色和红色各自独立触发和清理
            for level, threshold in [("orange", kd.alert_orange), ("red", kd.alert_red)]:
                if threshold is None:
                    continue
                alert_key = f"{user_name}:{kd.key}:{level}:current"
                alert_id = f"profile:{user_name}:{kd.key}:{level}"

                if computed >= threshold:
                    # 值满足阈值：如果未标记则触发告警
                    if not is_alert_marked(alert_key):
                        level_label = tr("橙色") if level == "orange" else tr("红色")
                        message = f"{user_name} {kd.label} [{level_label}] 已达 {int(computed)}，超过阈值 {threshold}"
                        # 先持久化告警，再标记去重（避免 add_alert 失败时去重标记已写入但告警未持久化）
                        add_alert(alert_id, message, datetime.now().isoformat())
                        mark_alert(alert_key, datetime.now().isoformat(timespec="seconds"))
                        # 信号仅用于通知 UI 刷新
                        self._notify_alert(kd.key, kd.label, message)
                else:
                    # 值低于阈值：如果已标记则清除（允许下次重新触发）
                    if is_alert_marked(alert_key):
                        unmark_alert(alert_key)
                        # 注意：不清理 alert_info，由用户手动关闭

        # ── 通知 UI 刷新（派生值变了，例如刚跨过周期边界）──
        # 只在「界面显示的数字真的变了」时发信号：跨边界那一刻刷新一次，
        # 之后条目进了新周期就不再重复刷新，不做无谓的整表重画。
        if self._quota_display.get(user_name) != display:
            self._quota_display[user_name] = display
            self._notify_updated(user_name)
