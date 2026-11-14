"""恢复登录态后应立刻同步一轮，而不是干等一个 60 秒节拍。

回归：``AccountHolder.apply()`` 只 ``start()`` 分钟定时器，首次触发要等满
一个 interval，刚打开客户端的人会以为「启动半天还没同步」。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication


def _holder(monkeypatch, *, paused: bool):
    from lvjiang.ui import account_dialog

    monkeypatch.setattr(account_dialog, "readonly_instance", lambda: False)
    monkeypatch.setattr(account_dialog, "sync_paused", lambda: paused)
    holder = account_dialog.AccountHolder()
    calls: list[bool] = []
    monkeypatch.setattr(holder, "sync_async",
                        lambda allow_overwrite=False, *, auto=False: calls.append(auto))
    return holder, calls


def test_apply_triggers_one_immediate_sync(qtbot, monkeypatch):
    holder, calls = _holder(monkeypatch, paused=False)

    holder.apply({"users": 0})
    QApplication.processEvents()
    assert calls == [True], "恢复登录态后应立刻自动同步一次"

    # 再次 apply（登录、刷新摘要都会走这里）不再补跑，交给分钟节拍。
    holder.apply({"users": 0})
    QApplication.processEvents()
    assert calls == [True]


def test_apply_does_not_sync_when_paused(qtbot, monkeypatch):
    holder, calls = _holder(monkeypatch, paused=True)

    holder.apply({"users": 0})
    QApplication.processEvents()
    assert calls == [], "暂停同步时不许自动跑"


def test_apply_without_summary_does_not_sync(qtbot, monkeypatch):
    holder, calls = _holder(monkeypatch, paused=False)

    holder.apply(None)          # 未登录 / 登录态已死
    QApplication.processEvents()
    assert calls == []
