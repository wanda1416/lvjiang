"""用户信息页的工具栏、模型分区与值展示。"""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QGroupBox

from lvjiang.core.profile.models import (
    MODEL_NOTE,
    MODEL_QUOTA,
    MODEL_REGEN,
    MODEL_STOCK,
    KeyDef,
    NoteKeyDef,
)
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.profile.user_info import UserInfoTab, _DetailPage, _format_detail_value


class _SignalStub:
    def connect(self, _callback):
        pass


class _HostStub:
    def __init__(self):
        self.user_changed = _SignalStub()
        self.user_combo = QComboBox()
        self.user_combo.addItem("测试用户")
        self.user_manager = object()
        self.alert_panel = None

    def active_user_name(self):
        return ""

    def navigate_user(self, _step):
        pass


def test_add_note_button_is_at_far_right_of_refresh_toolbar(qtbot, monkeypatch):
    engine = SimpleNamespace(
        data_updated=_SignalStub(),
        alert_triggered=_SignalStub(),
    )
    monkeypatch.setattr(
        "lvjiang.core.profile.engine.get_or_create_engine", lambda _manager: engine
    )
    tab = UserInfoTab(_HostStub())
    qtbot.addWidget(tab)
    qtbot.wait(1)  # 执行用户导航按钮的 singleShot 初始化

    toolbar = tab.layout().itemAt(0).layout()

    assert toolbar.itemAt(toolbar.count() - 1).widget() is tab._add_note_button
    assert toolbar.itemAt(toolbar.count() - 2).spacerItem() is not None


def test_user_info_font_size_only_changes_content_below_toolbar(qtbot, monkeypatch):
    engine = SimpleNamespace(
        data_updated=_SignalStub(),
        alert_triggered=_SignalStub(),
    )
    monkeypatch.setattr(
        "lvjiang.core.profile.engine.get_or_create_engine", lambda _manager: engine
    )
    tab = UserInfoTab(_HostStub())
    qtbot.addWidget(tab)
    qtbot.wait(1)
    toolbar_size = tab._add_note_button.font().pointSize()
    forwarded_sizes: list[int] = []
    tab._detail_page = SimpleNamespace(
        set_content_font_size=forwarded_sizes.append)

    tab.apply_content_font_size(18)

    assert tab._content_widget.font().pointSize() == 18
    placeholder = tab._detail_container.itemAt(0).widget()
    assert placeholder.font().pointSize() == 18
    assert tab._add_note_button.font().pointSize() == toolbar_size
    assert forwarded_sizes == [18]


def test_detail_page_shows_all_model_sections(qtbot, monkeypatch):
    config = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [KeyDef(key="quota_key", label="配额项")],
        MODEL_STOCK: [KeyDef(key="stock_key", label="库存项")],
        MODEL_REGEN: [KeyDef(key="regen_key", label="再生项")],
        MODEL_NOTE: [NoteKeyDef(key="note_key", label="备注项")],
    })
    monkeypatch.setattr(
        "lvjiang.core.profile.get_profile_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.profile.user_info.db_read_all",
        lambda _user: {MODEL_NOTE: {
            "note_key": {"value": 0.0, "value_text": "测试备注"},
        }},
    )

    manager = SimpleNamespace(
        get_user=lambda _name: SimpleNamespace(avatar=""),
    )
    page = _DetailPage("测试用户", manager)
    qtbot.addWidget(page)

    boxes = page.findChildren(QGroupBox)
    assert [box.title() for box in boxes] == [
        "用户头像", "便利贴", "配额", "库存", "再生", "备注",
    ]
    assert len({box.styleSheet() for box in boxes}) == 1
    assert "font-weight: bold" in boxes[0].styleSheet()
    assert page._value_labels["note_key"].text() == "测试备注"
    notes_item = page._profile_row.itemAt(
        page._profile_row.indexOf(page._notes_panel))
    assert notes_item.alignment() & Qt.AlignmentFlag.AlignTop
    assert page._profile_row.indexOf(page._avatar_box) == 0
    assert page._profile_row.indexOf(page._notes_panel) == 1


def test_detail_page_defers_avatar_file_loading(qtbot, monkeypatch):
    config = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [], MODEL_STOCK: [], MODEL_REGEN: [], MODEL_NOTE: [],
    })
    monkeypatch.setattr(
        "lvjiang.core.profile.get_profile_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.profile.user_info.db_read_all", lambda _user: {})
    manager = SimpleNamespace(
        get_user=lambda _name: SimpleNamespace(avatar="saved-avatar.png"),
    )

    page = _DetailPage("测试用户", manager)
    qtbot.addWidget(page)

    # Constructor and synchronous profile loading only create the placeholder;
    # avatar disk lookup is queued until the panel has had a chance to render.
    assert page._avatar.filename == ""
    qtbot.waitUntil(
        lambda: page._avatar.filename == "saved-avatar.png", timeout=500)


def test_detail_avatar_opens_editor_directly_and_returns_to_page(
    qtbot, monkeypatch,
):
    config = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [], MODEL_STOCK: [], MODEL_REGEN: [], MODEL_NOTE: [],
    })
    monkeypatch.setattr(
        "lvjiang.core.profile.get_profile_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.profile.user_info.db_read_all", lambda _user: {})
    opened: list[tuple] = []

    class _EditorSignal:
        def connect(self, callback):
            self._callback = callback

        def emit(self, *args):
            self._callback(*args)

    class _Editor:
        def __init__(self, username, manager, parent, **kwargs):
            opened.append((username, manager, parent, kwargs))
            self.avatar_changed = _EditorSignal()

        def exec(self):
            self.avatar_changed.emit("测试用户", "new-avatar.png")

    monkeypatch.setattr(
        "lvjiang.ui.profile.user_info.AvatarEditorDialog", _Editor)
    manager = SimpleNamespace(
        get_user=lambda _name: SimpleNamespace(avatar="old-avatar.png"),
    )

    def screenshot_callback():
        return None

    page = _DetailPage(
        "测试用户", manager, screenshot_callback=screenshot_callback)
    qtbot.addWidget(page)

    page._avatar.edit_requested.emit()
    qtbot.wait(40)

    assert len(opened) == 1
    username, passed_manager, parent, kwargs = opened[0]
    assert username == "测试用户"
    assert passed_manager is manager
    assert parent is page
    assert kwargs["screenshot_callback"] is screenshot_callback
    assert page._avatar.filename == "new-avatar.png"


def test_detail_note_value_uses_text_storage():
    kd = NoteKeyDef(key="note_key", label="备注项")
    data = {MODEL_NOTE: {
        "note_key": {"value": 0.0, "value_text": "需要补充材料"},
    }}

    assert _format_detail_value(kd, MODEL_NOTE, data) == "需要补充材料"
