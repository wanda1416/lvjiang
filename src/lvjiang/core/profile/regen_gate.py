"""周期恢复（regen）的写入闸门。

事故背景（2026-11-09）：``ProfileEngine`` 启动即 tick，而首次 pull 在 60~80 秒后才
发生。窗口内本地锚点还是上一次同步时的状态，于是「用过期基线算出的准点恢复」被写成
绝对值的 ``set`` 并上行；接收端只按时间戳判新旧，假值就覆盖了另一台机器上真实发生的
消耗（体力：450 被 2500 覆盖）。

**为什么不需要额外的幂等表**：周期恢复的幂等本来就由条目锚点承担——
``compute_boundary_value`` 数的是「锚点到 now 跨过几个边界」。只要保证「先同步、再推
算」，同步到位后锚点已落在本周期，边界数就是 0，值不变即不写。所以这里只做一件事：
**在本次进程完成首次成功同步之前，禁止推算/物化周期恢复**。

判定规则：
- 已经完成首次成功 pull → 放行（标记是进程内的，重启后重新要求先同步，换账号也重置）；
- 确实没有凭据（永远不会上行）→ 放行：此时本地推进没有污染面，
  若一律拦住，不登录的用户体力将永远不增长；
- 有凭据但还没拉到最新，或登录态读不到（凭据库异常）→ 拦住，
  宁可让恢复暂停一会儿，也不写坏跨端数据。

只影响周期恢复；普通观测写入、消耗 ``delta``、``quota`` 周期重置都不经过这里。
"""

from __future__ import annotations

import time

#: 登录态检查结果缓存秒数：探针会读系统凭据库，不宜在每次写入时都付一次开销。
#: 登录/登出经 ``account.service._store_refresh_token`` 主动作废，不靠 TTL 兜底。
_SESSION_CACHE_TTL = 60.0

_pull_completed = False
_session_cache: tuple[float, bool | None] | None = None


def mark_pull_completed() -> None:
    """标记本次进程已完成一次「干净」的远端拉取。

    只在 ``pull_and_replay`` 无错误、无待决、且回放行已落地后调用；
    失败或中止时不得调用，闸门继续保持关闭。
    """
    global _pull_completed
    _pull_completed = True


def pull_completed() -> bool:
    """本次进程是否已完成首次成功拉取（诊断与测试用）。"""
    return _pull_completed


def _session_state() -> bool | None:
    """本地登录态：``True`` 有凭据、``False`` 确实没有、``None`` 读不到。

    凭据后端异常时返回 None 而不是 False——闸门据此**保守拦住**：
    内存里可能仍有可上行的 access token，误判成"未登录"会放行本不该产生的
    派生物化（本次评审意见 ③）。
    """
    global _session_cache
    now = time.monotonic()
    if _session_cache is not None and now - _session_cache[0] < _SESSION_CACHE_TTL:
        return _session_cache[1]
    from ..account.service import stored_session_state

    try:
        value = stored_session_state()
    except Exception:  # noqa: BLE001 - 探针本身异常同样按"未知"处理
        value = None
    _session_cache = (now, value)
    return value


def invalidate_session_cache() -> None:
    """登录/登出/恢复登录后丢弃缓存的登录态。

    由 ``account.service._store_refresh_token`` 统一调用——它是登录、restore、
    logout、logout_all 与手机端共用的唯一收口。
    """
    global _session_cache
    _session_cache = None


def period_recovery_allowed() -> bool:
    """当前是否允许推算并物化周期恢复。

    - 本进程已完成首次干净拉取 → 放行（幂等由条目锚点承担）；
    - 确实没有凭据（不会上行）→ 放行，否则不登录的用户永远不会恢复；
    - 有凭据但还没拉到最新 / 登录态读不到 → 拦住（宁可暂停，也不写坏数据）。
    """
    if _pull_completed:
        return True
    return _session_state() is False


def reset() -> None:
    """清空进程内状态（测试用；换账号时也用它重新关闭闸门）。"""
    global _pull_completed, _session_cache
    _pull_completed = False
    _session_cache = None
