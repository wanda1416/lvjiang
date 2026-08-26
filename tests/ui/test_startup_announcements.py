"""启动公告必须先于版本更新，并在展示后才记录版本。"""
from types import SimpleNamespace

from lvjiang.core.announcement import (
    Announcement,
    AnnouncementFetchResult,
    AnnouncementManifest,
)
from lvjiang.ui.main_window import MainWindow


class FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, value):
        for callback in self._callbacks:
            callback(value)


def test_startup_shows_announcement_then_checks_update(monkeypatch):
    import lvjiang.core.announcement as core
    import lvjiang.ui.notices.announcement_dialog as dialog_module

    notice = Announcement("urgent", "critical", "紧急公告", "正文")
    manifest = AnnouncementManifest(1, 5, "", (notice,))
    events = []

    class FakeChecker:
        def __init__(self, parent):
            self.finished = FakeSignal()
            self.error = FakeSignal()

        def start(self):
            self.finished.emit(AnnouncementFetchResult(manifest, etag='"v5"'))

    class FakeDialog:
        def __init__(self, received, notices, parent, allow_refresh):
            assert received is manifest
            assert notices == (notice,)
            assert allow_refresh is False

        def exec(self):
            events.append("dialog")

    monkeypatch.setattr(core, "AnnouncementChecker", FakeChecker)
    monkeypatch.setattr(core, "cache_manifest", lambda *args: events.append("cache"))
    monkeypatch.setattr(core, "should_prompt_manifest", lambda value: True)
    monkeypatch.setattr(core, "applicable_notices", lambda value: (notice,))
    monkeypatch.setattr(
        core, "mark_notice_version", lambda version: events.append(("mark", version)))
    monkeypatch.setattr(dialog_module, "AnnouncementDialog", FakeDialog)

    host = SimpleNamespace(
        _start_update_check_on_startup=lambda: events.append("update"))
    MainWindow.check_update_on_startup(host)

    assert events == ["cache", "dialog", ("mark", 5), "update"]


def test_startup_announcement_failure_still_checks_update(monkeypatch):
    import lvjiang.core.announcement as core
    events = []

    class FakeChecker:
        def __init__(self, parent):
            self.finished = FakeSignal()
            self.error = FakeSignal()

        def start(self):
            self.error.emit("offline")

    monkeypatch.setattr(core, "AnnouncementChecker", FakeChecker)
    host = SimpleNamespace(
        _start_update_check_on_startup=lambda: events.append("update"))

    MainWindow.check_update_on_startup(host)

    assert events == ["update"]
