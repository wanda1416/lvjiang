"""窗口标题自动连接不得误选其他律匠实例。"""

from lvjiang.ui.main.window_ops import _find_auto_connect_window_index


def test_auto_connect_skips_lvjiang_window_with_same_keyword() -> None:
    windows = [
        {"title": "律匠 - 燕云十六声装备调律工具"},
        {"title": "燕云十六声"},
    ]

    assert _find_auto_connect_window_index(windows, "燕云") == 1


def test_auto_connect_rejects_lvjiang_only_match() -> None:
    windows = [{"title": "律匠 - 手机投屏诊断"}]

    assert _find_auto_connect_window_index(windows, "手机投屏") is None
