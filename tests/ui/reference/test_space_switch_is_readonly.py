"""图库管理器的空间下拉只切「正在编辑的空间」，不改运行时空间。"""

from __future__ import annotations

from lvjiang.ui.reference.dialog import ReferenceManagerDialog


def test_space_combo_open_does_not_activate(qtbot, monkeypatch):
    # 预览/测试实例：不写真实 session 的界面参数
    dialog = ReferenceManagerDialog(persist_ui_state=False)
    qtbot.addWidget(dialog)
    db = dialog._db
    calls: dict[str, str] = {}

    def open_space(name: str) -> bool:
        calls["open"] = name
        return True

    def set_active_space(name: str) -> bool:      # 管理界面不得调用
        raise AssertionError(f"图库界面改写了运行时空间: {name}")

    monkeypatch.setattr(db, "open_space", open_space)
    monkeypatch.setattr(db, "set_active_space", set_active_space)
    monkeypatch.setattr(dialog, "_refresh_panels", lambda: None)

    combo = dialog._space_combo
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("甲", "space-a")
    combo.addItem("乙", "space-b")
    combo.blockSignals(False)

    dialog._on_space_changed(1)

    assert calls["open"] == "space-b"
