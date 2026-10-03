"""随包工具与本地采集产出的目录契约。"""

from unittest.mock import MagicMock

from lvjiang.constants import PICTURE_DIR, PROJECT_ROOT, VIDEO_DIR
from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture


def test_scrcpy_server_ships_inside_adb_directory() -> None:
    """scrcpy server 与 adb 一起分发，不再维护单文件目录。"""
    capture = AndroidStreamCapture(MagicMock())
    expected = PROJECT_ROOT / "data" / "adb" / "scrcpy-server.jar"

    assert capture._jar_local == expected
    assert expected.is_file()
    assert not (PROJECT_ROOT / "data" / "scrcpy").exists()


def test_capture_outputs_share_capture_directory() -> None:
    """用户截屏与录屏集中存放，但仍按媒体类型分目录。"""
    assert PICTURE_DIR == PROJECT_ROOT / "data" / "capture" / "picture"
    assert VIDEO_DIR == PROJECT_ROOT / "data" / "capture" / "video"
