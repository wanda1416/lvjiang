"""场景管理第二行尺寸信息栏文案格式单元测试。"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from lvjiang.ui.scene_editor import dialog as dialog_mod
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog


@pytest.fixture(autouse=True)
def _needs_qapp(qapp):
    """``SceneEditorDialog`` 的尺寸信息栏会读主题，需要存在 QApplication。"""


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


class _DummyLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


def _make_dummy_dialog(
    *,
    image_size: tuple[int, int] = (2800, 1260),
    w_ratio: float = 1.0,
    h_ratio: float = 1.0,
    current_screenshot_index: int = 1,
    has_layout: bool = True,
) -> SimpleNamespace:
    tab = SimpleNamespace(
        scene_key="bag",
        current_view="default",
        current_screenshot_index=current_screenshot_index,
        canvas=SimpleNamespace(image_size=image_size),
        get_canvas_config=lambda: SimpleNamespace(w_ratio=w_ratio, h_ratio=h_ratio),
    )
    return SimpleNamespace(
        _info_label=_DummyLabel(),
        _current_layout=SimpleNamespace(key="test_layout") if has_layout else None,
        _current_scene_tab=lambda: tab,
        _set_empty_info_label=lambda: None,
        _fmt_ratio=SceneEditorDialog._fmt_ratio,
    )


@pytest.mark.parametrize(
    ("indices", "current_idx", "expected_suffix"),
    [
        ([1], 1, "截图 1/1"),
        ([1, 2], 2, "截图 2/2"),
        ([1, 2, 3], 2, "截图 2/3"),
        ([1, 3], 3, "截图 2/2"),
        ([], 1, "截图 1/1"),
    ],
)
def test_update_info_label_format_and_screenshot_counter(
    monkeypatch: pytest.MonkeyPatch,
    indices: list[int],
    current_idx: int,
    expected_suffix: str,
) -> None:
    monkeypatch.setattr(
        dialog_mod,
        "list_scene_screenshots",
        lambda layout_key, scene_key, view: indices,
    )
    dummy = _make_dummy_dialog(current_screenshot_index=current_idx)
    SceneEditorDialog._update_info_label(dummy)  # type: ignore[arg-type]

    plain = _strip_html(dummy._info_label.text)
    assert plain == (
        f"2800×1260 (20:9, 2.22) │ 画布 2800×1260 (20:9, 2.22) │ {expected_suffix}"
    )


def test_update_info_label_without_layout_still_shows_1_of_1() -> None:
    dummy = _make_dummy_dialog(has_layout=False)
    SceneEditorDialog._update_info_label(dummy)  # type: ignore[arg-type]

    plain = _strip_html(dummy._info_label.text)
    assert plain == "2800×1260 (20:9, 2.22) │ 画布 2800×1260 (20:9, 2.22) │ 截图 1/1"
