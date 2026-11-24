"""界面偏好保存失败：明确提示、只提示一次、不把点击变成崩溃。

历史崩溃：点击装备槽位保存筛选时，另一个进程正持有该用户的执行租约，
``AccessDeniedError`` 直接冒到 Qt 的 ``mousePressEvent``，整个客户端退出。

这里不构造真实页面：``_save_user_filter`` 只依赖 ``_inv`` 与提示方法，
用轻量替身即可覆盖「失败不抛异常、提示只出一次」这条契约。
"""
from __future__ import annotations

import types

from PyQt6.QtWidgets import QMessageBox

from lvjiang.apps.yysls.ui.loadout.equip.status_tab import EquipStatusTab

_BUSY = "用户「测试」正在执行任务，请稍后重试"


class _BrokenRepo:
    """模拟写入被拒的仓储。"""

    def __init__(self) -> None:
        self.calls = 0

    def set_ui_state(self, key: str, value: dict) -> None:
        self.calls += 1
        raise OSError(_BUSY)


def _bare_tab(monkeypatch, warnings: list) -> tuple[types.SimpleNamespace, _BrokenRepo]:
    repo = _BrokenRepo()
    tab = types.SimpleNamespace(_inv=types.SimpleNamespace(_repo=repo))
    tab._notify_pref_save_failed = (
        lambda exc: EquipStatusTab._notify_pref_save_failed(tab, exc))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args)))
    return tab, repo


def test_filter_save_failure_warns_once_without_raising(monkeypatch):
    """保存失败只提示一次，用户仍能继续点槽位和切筛选。"""
    warnings: list = []
    tab, repo = _bare_tab(monkeypatch, warnings)

    EquipStatusTab._save_user_filter(tab, {"sort": "level"})
    EquipStatusTab._save_user_filter(tab, {"sort": "quality"})

    assert repo.calls == 2
    assert len(warnings) == 1
    assert "筛选条件未保存" in warnings[0][1]
    assert _BUSY in warnings[0][2]
