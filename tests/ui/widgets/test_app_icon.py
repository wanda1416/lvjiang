"""桌面端应用图标：资产随包、启动时挂得上。

回归目标：源码运行没有窗口图标（全仓无 setWindowIcon、也无位图资产），任务栏
只显示 python.exe 的通用图标。这里锁住「资产在包内能找到」与「挂到 QApplication
上非空」两条最小的可观察契约；打包布局的解析顺序由 ``_candidate_paths`` 覆盖，
真实 PyInstaller 布局由发布流水线验证。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QGuiApplication, QIcon

from lvjiang.ui.widgets.app_icon import (
    DESKTOP_FILE_NAME,
    ICON_FILE,
    apply_app_icon,
    icon_path,
    set_linux_desktop_file_name,
    set_windows_app_user_model_id,
)


def test_icon_asset_ships_inside_package():
    """资产必须落在包目录里，否则源码运行与打包都找不到。"""
    path = icon_path()

    assert path is not None and path.is_file()
    assert path.name == ICON_FILE
    assert path.parent.name == "assets"
    assert path.parent.parent.name == "lvjiang"


def test_apply_app_icon_sets_non_null_window_icon(qapp):
    """挂到应用上的图标非空且有多个尺寸（Windows 会按 DPI 挑）。"""
    previous = qapp.windowIcon()
    try:
        assert apply_app_icon(qapp) is True
        icon = qapp.windowIcon()
        assert not icon.isNull()
        assert icon.availableSizes()
        assert icon_path() is not None
        sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        assert (256, 256) in sizes
    finally:
        qapp.setWindowIcon(previous)


def test_missing_asset_is_not_fatal(monkeypatch, tmp_path):
    """资产缺失时返回 False 而不抛错：图标缺失不能挡住启动。"""
    monkeypatch.setattr(
        "lvjiang.ui.widgets.app_icon._candidate_paths",
        lambda: iter([tmp_path / "nope" / ICON_FILE]),
    )

    assert icon_path() is None
    assert apply_app_icon(object()) is False


def test_linux_desktop_file_name_matches_packaged_entry(qapp, monkeypatch):
    """Wayland 只按 app_id 找桌面入口，名字必须与随包的 lvjiang.desktop 一致。"""
    monkeypatch.setattr("lvjiang.ui.widgets.app_icon.sys.platform", "linux")

    set_linux_desktop_file_name()

    assert DESKTOP_FILE_NAME == "lvjiang"
    assert QGuiApplication.desktopFileName() == DESKTOP_FILE_NAME


def test_windows_app_user_model_id_is_noop_off_windows(monkeypatch):
    """非 Windows 下直接返回，不去碰 shell32。"""
    monkeypatch.setattr("lvjiang.ui.widgets.app_icon.sys.platform", "linux")

    set_windows_app_user_model_id()


def test_icon_ico_exists_next_to_png():
    """exe 与安装包用 .ico；缺了它打包会回落到默认图标。"""
    png = icon_path()
    assert png is not None
    ico = Path(png).with_suffix(".ico")

    assert ico.is_file()
    assert QIcon(str(ico)).availableSizes()
