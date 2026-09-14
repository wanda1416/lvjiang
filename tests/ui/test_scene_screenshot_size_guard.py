from types import SimpleNamespace

import numpy as np
from PyQt6.QtWidgets import QMessageBox

from lvjiang.ui.scene_editor.dialog import SceneEditorDialog


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, message):
        self.message = message


class _Canvas:
    def __init__(self):
        self.image = None

    def set_image(self, image):
        self.image = image


class _RefreshHarness:
    _on_refresh_image = SceneEditorDialog._on_refresh_image

    def __init__(self, image):
        self._refresh_callback = lambda: (image, None)
        self._current_layout = SimpleNamespace(name="默认布局")
        self._current_scene_key = "game_main_page"
        self._status_bar = _StatusBar()
        self._tabs = {
            self._current_scene_key: SimpleNamespace(
                current_view="base", canvas=_Canvas())
        }
        self._img_cache = {}
        self._loaded_scenes = set()

    def _update_info_label(self):
        pass


def test_refresh_rejects_mismatched_existing_screenshot_by_default(monkeypatch):
    old_image = np.zeros((720, 1280, 3), dtype=np.uint8)
    new_image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    harness = _RefreshHarness(new_image)
    saved = []
    questions = []
    events = []
    harness._refresh_callback = lambda: (
        events.append("capture") or new_image,
        None,
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.load_scene_screenshot",
        lambda *_args: old_image,
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.save_scene_screenshot",
        lambda *args: saved.append(args),
    )

    def answer_no(*args):
        events.append("confirm")
        questions.append(args)
        assert args[-1] == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", answer_no)

    harness._on_refresh_image()

    assert len(questions) == 1
    assert events == ["capture", "confirm"]
    assert "1280 × 720" in questions[0][2]
    assert "1920 × 1080" in questions[0][2]
    assert saved == []
    assert harness._tabs["game_main_page"].canvas.image is None
    assert harness._status_bar.message == "已取消刷新截图"


def test_refresh_same_size_does_not_prompt(monkeypatch):
    old_image = np.zeros((720, 1280, 3), dtype=np.uint8)
    new_image = np.ones((720, 1280, 3), dtype=np.uint8)
    harness = _RefreshHarness(new_image)
    saved = []
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.load_scene_screenshot",
        lambda *_args: old_image,
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.save_scene_screenshot",
        lambda *args: saved.append(args),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应询问")),
    )

    harness._on_refresh_image()

    assert len(saved) == 1
    assert harness._tabs["game_main_page"].canvas.image is new_image
    assert harness._img_cache[("默认布局", "game_main_page", "base")] is new_image


def test_refresh_saves_mismatched_screenshot_after_confirmation(monkeypatch):
    old_image = np.zeros((720, 1280, 3), dtype=np.uint8)
    new_image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    harness = _RefreshHarness(new_image)
    saved = []
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.load_scene_screenshot",
        lambda *_args: old_image,
    )
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.save_scene_screenshot",
        lambda *args: saved.append(args),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    harness._on_refresh_image()

    assert len(saved) == 1
    assert harness._tabs["game_main_page"].canvas.image is new_image
