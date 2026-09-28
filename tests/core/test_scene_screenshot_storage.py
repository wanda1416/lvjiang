"""场景编辑器截图格式与多截图文件操作。"""

import cv2
import numpy as np
import pytest

from lvjiang.core import layout_manager as screenshots


def _frame(value: int) -> np.ndarray:
    return np.full((64, 96, 3), value, dtype=np.uint8)


def test_new_screenshots_default_to_webp_and_png_can_replace_one_view(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", tmp_path)
    assert screenshots.get_scene_screenshot_format("layout", "scene", "detail") == "webp"

    stored = screenshots.save_scene_screenshot(
        "layout", "scene", _frame(100), "detail")
    directory = tmp_path / "layout"
    assert (directory / "scene__detail.webp").is_file()
    assert screenshots.list_scene_screenshots("layout", "scene", "detail") == [1]
    assert screenshots.load_scene_screenshot("layout", "scene", "detail").shape == (64, 96, 3)
    assert np.array_equal(
        stored, screenshots.load_scene_screenshot("layout", "scene", "detail"))

    screenshots.set_scene_screenshot_format("layout", "scene", "detail", "png")
    assert screenshots.get_scene_screenshot_format("layout", "scene", "detail") == "png"
    assert screenshots.get_scene_screenshot_format("layout", "scene", "") == "webp"
    screenshots.save_scene_screenshot("layout", "scene", _frame(120), "detail")
    assert (directory / "scene__detail.png").is_file()
    assert not (directory / "scene__detail.webp").exists()
    assert np.array_equal(
        screenshots.load_scene_screenshot("layout", "scene", "detail"),
        _frame(120),
    )


def test_legacy_png_and_webp_can_be_reordered_and_deleted_together(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", tmp_path)
    directory = tmp_path / "layout"
    directory.mkdir()
    ok, encoded = cv2.imencode(".png", _frame(10))
    assert ok
    (directory / "scene.png").write_bytes(encoded.tobytes())
    screenshots.save_scene_screenshot("layout", "scene", _frame(20), index=2)

    assert screenshots.list_scene_screenshots("layout", "scene") == [1, 2]
    assert np.array_equal(screenshots.load_scene_screenshot("layout", "scene"), _frame(10))
    screenshots.reindex_scene_screenshots("layout", "scene", "", 1, 2)
    assert (directory / "scene.webp").exists()
    assert (directory / "scene__2.png").exists()
    assert np.array_equal(screenshots.load_scene_screenshot("layout", "scene", index=2), _frame(10))

    screenshots.delete_scene_screenshot("layout", "scene", "", 1)
    assert screenshots.list_scene_screenshots("layout", "scene") == [1]
    assert (directory / "scene.png").exists()
    assert not (directory / "scene.webp").exists()


def test_scene_and_view_rename_keep_screenshot_format_preference(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", tmp_path)
    screenshots.set_scene_screenshot_format("layout", "scene", "old_view", "png")
    screenshots.save_scene_screenshot("layout", "scene", _frame(30), "old_view")
    screenshots.rename_view_screenshots("scene", "old_view", "new_view")
    assert screenshots.get_scene_screenshot_format("layout", "scene", "new_view") == "png"
    screenshots.rename_scene_screenshots("scene", "new_scene")
    assert screenshots.get_scene_screenshot_format("layout", "new_scene", "new_view") == "png"
    assert (tmp_path / "layout" / "new_scene__new_view.png").exists()


def test_failed_webp_encode_keeps_previous_png(tmp_path, monkeypatch):
    monkeypatch.setattr(screenshots, "SCREENSHOTS_DIR", tmp_path)
    directory = tmp_path / "layout"
    directory.mkdir()
    ok, encoded = cv2.imencode(".png", _frame(10))
    assert ok
    legacy = directory / "scene.png"
    legacy.write_bytes(encoded.tobytes())
    original = legacy.read_bytes()
    monkeypatch.setattr(cv2, "imencode", lambda *_args: (False, None))

    with pytest.raises(OSError, match="截图编码失败"):
        screenshots.save_scene_screenshot("layout", "scene", _frame(20))

    assert legacy.read_bytes() == original
    assert not (directory / "scene.webp").exists()
