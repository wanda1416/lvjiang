"""基础配置的四组单选：中文与主界面同口径，英文一律保留，第二列纵向对齐。

中文要和主界面「状态信息」列同一套叫法（前台输入 / 后台输入 / 指令执行 /
端侧执行 / 单帧截图 / 流式截图），否则同一件事在两个页面叫两个名字。

英文（SendInput / PostMessage / mss / WGC / ADB shell input / Scrcpy /
ADB screencap）不能删：这是配置页，写着底层用的是哪条通道，新来的人、以及拿
截图去问 AI 的人都靠它对上文档。主界面那一列才只留四个汉字。
"""
import pytest
from PyQt6.QtWidgets import QRadioButton

from lvjiang.ui.settings_dialog import SettingsDialog, _align_radio_columns

pytestmark = pytest.mark.usefixtures("qapp")


def _rows(dialog: SettingsDialog) -> list[tuple[QRadioButton, QRadioButton]]:
    return [
        (dialog._input_fg_radio, dialog._input_bg_radio),
        (dialog._desktop_capture_fg_radio, dialog._desktop_capture_bg_radio),
        (dialog._android_input_adb_radio, dialog._android_input_agent_radio),
        (dialog._capture_stream_radio, dialog._capture_static_radio),
    ]


def test_chinese_wording_matches_the_main_page(qtbot) -> None:
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)

    labels = [button.text() for row in _rows(dialog) for button in row]

    for word in ("前台输入", "后台输入", "指令执行", "端侧执行",
                 "单帧截图", "流式截图", "前台截图", "后台截图"):
        assert any(word in label for label in labels), word
    # 主界面改名之前的旧叫法不该再出现
    for stale in ("光标输入", "设备端执行", "静态截图"):
        assert not any(stale in label for label in labels), stale


def test_backend_names_stay_in_the_labels(qtbot) -> None:
    """英文是这一页的价值所在，不能为了四字对齐删掉。"""
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)

    labels = [button.text() for row in _rows(dialog) for button in row]

    for backend in ("SendInput", "PostMessage", "mss", "WGC",
                    "ADB shell input", "Scrcpy", "ADB screencap"):
        assert any(backend in label for label in labels), backend


def test_second_radio_of_every_row_is_aligned(qtbot) -> None:
    """四行的第二个单选要成列。

    第一个单选的文字长短差很多（「前台截图 (mss)」比
    「ADB shell input 指令执行」短一截），HBoxLayout 紧挨着排的话第二列各行
    各样。第一列按最宽的撑成等宽，第二列自然对齐。
    """
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)

    widths = {first.minimumWidth() for first, _ in _rows(dialog)}

    assert len(widths) == 1, widths
    assert widths.pop() > 0


def test_alignment_follows_the_widest_label() -> None:
    """按 sizeHint 算而不是写死像素——这些文案要过 i18n，英文下宽度不同。"""
    short = QRadioButton("短")
    long_one = QRadioButton("很长很长很长很长的一项文案")
    tail = QRadioButton("第二项")

    _align_radio_columns([short, tail], [long_one, tail])

    assert short.minimumWidth() == long_one.minimumWidth()
    assert short.minimumWidth() >= long_one.sizeHint().width()
