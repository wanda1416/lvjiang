"""图库空间栏：系统空间置灰 + 删除空间按钮的启停与确认流

只驱动被测方法本体，绕开对话框的重量级 __init__
（三个 Tab + 真实 config 读取），保证测试不触碰真实图库。
"""

from PyQt6.QtWidgets import QComboBox, QMessageBox, QPushButton

from lvjiang.ui.reference.dialog import ReferenceManagerDialog


class _FakeDB:
    """按 ReferenceDatabase 的空间契约打桩：系统空间不可删、至少留一个"""

    def __init__(self, spaces, system_spaces, user_mode, active=None):
        self._spaces = list(spaces)
        self._system = set(system_spaces)
        self._user_mode = user_mode
        self._active = active or (self._spaces[0] if self._spaces else "")
        self.entries = []
        self.deleted = []

    def get_spaces(self):
        return list(self._spaces)

    def get_active_space(self):
        return self._active

    def is_system_space(self, name):
        return name in self._system

    def is_user_mode(self):
        return self._user_mode

    def can_delete_space(self, name):
        if name not in self._spaces:
            return "空间不存在"
        if self._user_mode and name in self._system:
            return "系统空间不可删除，可新建自己的空间"
        if len(self._spaces) <= 1:
            return "至少保留一个图库空间"
        return ""

    def delete_space(self, name):
        if self.can_delete_space(name):
            return False
        self.deleted.append(name)
        self._spaces.remove(name)
        if self._active == name:
            self._active = self._spaces[0]
        return True


def _dialog(qtbot, db):
    """构造只挂了空间栏控件的对话框壳（跳过重量级 __init__）"""
    dlg = ReferenceManagerDialog.__new__(ReferenceManagerDialog)
    combo = QComboBox()
    qtbot.addWidget(combo)
    dlg._space_combo = combo
    dlg._btn_del_space = QPushButton()
    qtbot.addWidget(dlg._btn_del_space)
    dlg._db = db
    dlg._refresh_panels = lambda: None  # 面板刷新依赖三个 Tab，此处不在测试范围
    dlg._fill_space_combo()
    combo.setCurrentText(db.get_active_space())
    return dlg


def _fill(qtbot, spaces, system_spaces, user_mode):
    return _dialog(qtbot, _FakeDB(spaces, system_spaces, user_mode))._space_combo


def _is_gray(combo, row) -> bool:
    """是否显式设过前景色（未设时 ForegroundRole 为 None，走主题默认色）"""
    from PyQt6.QtCore import Qt
    return combo.model().item(row).data(Qt.ItemDataRole.ForegroundRole) is not None


class TestSpaceComboGraying:
    def test_user_mode_grays_system_spaces_only(self, qtbot):
        combo = _fill(qtbot, ["默认", "手游", "我的空间"], ["默认", "手游"], True)
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "默认", "手游", "我的空间"]
        assert _is_gray(combo, 0) and _is_gray(combo, 1)
        assert not _is_gray(combo, 2)
        assert combo.model().item(0).toolTip()  # 置灰项带原因说明

    def test_dev_mode_grays_nothing(self, qtbot):
        combo = _fill(qtbot, ["默认", "我的空间"], ["默认"], False)
        assert not _is_gray(combo, 0)
        assert not _is_gray(combo, 1)

    def test_refill_clears_previous_items(self, qtbot):
        combo = _fill(qtbot, ["默认"], ["默认"], True)
        combo.addItem("脏数据")
        dlg = ReferenceManagerDialog.__new__(ReferenceManagerDialog)
        dlg._space_combo = combo
        dlg._db = _FakeDB(["默认", "新空间"], ["默认"], True)
        dlg._fill_space_combo()
        assert [combo.itemText(i) for i in range(combo.count())] == ["默认", "新空间"]


class TestDeleteSpaceButton:
    def test_disabled_for_system_space_with_reason(self, qtbot):
        """用户模式选中系统空间：按钮禁用，tooltip 就是拒绝原因"""
        dlg = _dialog(qtbot, _FakeDB(["默认", "我的空间"], ["默认"], True))
        dlg._space_combo.setCurrentText("默认")
        dlg._refresh_del_space_enabled()
        assert not dlg._btn_del_space.isEnabled()
        assert "系统空间" in dlg._btn_del_space.toolTip()

    def test_enabled_for_local_space(self, qtbot):
        dlg = _dialog(qtbot, _FakeDB(["默认", "我的空间"], ["默认"], True))
        dlg._space_combo.setCurrentText("我的空间")
        dlg._refresh_del_space_enabled()
        assert dlg._btn_del_space.isEnabled()

    def test_disabled_when_only_one_space_left(self, qtbot):
        dlg = _dialog(qtbot, _FakeDB(["我的空间"], [], True))
        dlg._refresh_del_space_enabled()
        assert not dlg._btn_del_space.isEnabled()
        assert "至少保留" in dlg._btn_del_space.toolTip()

    def test_dev_mode_can_delete_system_space(self, qtbot):
        dlg = _dialog(qtbot, _FakeDB(["默认", "手游"], ["默认", "手游"], False))
        dlg._space_combo.setCurrentText("手游")
        dlg._refresh_del_space_enabled()
        assert dlg._btn_del_space.isEnabled()

    def test_confirm_yes_deletes_and_refills_combo(self, qtbot, monkeypatch):
        db = _FakeDB(["默认", "我的空间"], ["默认"], True)
        dlg = _dialog(qtbot, db)
        dlg._space_combo.setCurrentText("我的空间")
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        dlg._on_delete_space()
        assert db.deleted == ["我的空间"]
        assert [dlg._space_combo.itemText(i)
                for i in range(dlg._space_combo.count())] == ["默认"]
        assert dlg._space_combo.currentText() == "默认"
        assert not dlg._btn_del_space.isEnabled()  # 只剩一个空间

    def test_confirm_no_keeps_space(self, qtbot, monkeypatch):
        db = _FakeDB(["默认", "我的空间"], ["默认"], True)
        dlg = _dialog(qtbot, db)
        dlg._space_combo.setCurrentText("我的空间")
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.No)
        dlg._on_delete_space()
        assert db.deleted == []
        assert "我的空间" in db.get_spaces()

    def test_refuses_without_asking_when_protected(self, qtbot, monkeypatch):
        """系统空间：连确认框都不弹，直接给拒绝原因"""
        db = _FakeDB(["默认", "我的空间"], ["默认"], True)
        dlg = _dialog(qtbot, db)
        dlg._space_combo.setCurrentText("默认")

        def _boom(*a, **k):
            raise AssertionError("受保护空间不应弹确认框")

        monkeypatch.setattr(QMessageBox, "question", _boom)
        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda _p, _t, text, *a, **k: warned.append(text))
        dlg._on_delete_space()
        assert db.deleted == []
        assert warned and "系统空间" in warned[0]
