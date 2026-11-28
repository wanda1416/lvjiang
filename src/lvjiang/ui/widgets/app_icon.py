"""桌面端应用与窗口图标。

图标资产是 ``lvjiang/assets/icon.png`` / ``icon.ico``：形状与手机端的
``android/app/src/main/res/drawable/ic_launcher.xml`` 完全一致（圆底音叉），
配色按端区分——手机端是主色圆底（#006B5D）+ 白色音叉，桌面端把圆底换成浅一档
的绿（#07C160，微信绿），音叉同为白色。生成脚本见
``scripts/one_off/make_app_icon.py``。

源码运行与 PyInstaller 打包两种布局下 ``__file__`` 的位置不同，因此按
「包目录 → 解包目录 → 仓库源码树」依次尝试，找不到时返回空并保持旧行为
（不因为图标缺失影响启动）。
"""
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

ICON_FILE = "icon.png"

#: Linux 桌面入口文件名（不含 ``.desktop``）。Wayland 下窗口 app_id 必须与它一致，
#: 否则任务栏 / Dock 只显示通用图标。
DESKTOP_FILE_NAME = "lvjiang"

#: Windows 任务栏按 AppUserModelID 归类进程与窗口。不设置时，从源码运行的
#: ``python.exe`` 会沿用解释器的图标，窗口图标只对标题栏和 Alt+Tab 生效。
#: 值一经发布不要修改：改了以后用户已固定在任务栏的入口会变成两个。
APP_USER_MODEL_ID = "Lvjiang.Desktop"


def _candidate_paths() -> Iterator[Path]:
    """图标可能所在的路径，按可信度从高到低。"""
    # 包目录：源码是 src/lvjiang，PyInstaller 是 _MEIPASS/lvjiang
    yield Path(__file__).resolve().parents[2] / "assets" / ICON_FILE
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        yield Path(bundle_root) / "lvjiang" / "assets" / ICON_FILE
    if not getattr(sys, "frozen", False):
        # 源码树兜底：src/lvjiang/ui/widgets/app_icon.py → 仓库根
        yield Path(__file__).resolve().parents[3] / "src" / "lvjiang" / "assets" / ICON_FILE


def icon_path() -> Path | None:
    """返回实际可用的图标文件；找不到返回 ``None``。"""
    for candidate in _candidate_paths():
        if candidate.is_file():
            return candidate
    return None


def apply_app_icon(app) -> bool:
    """把图标挂到 QApplication 上（后续创建的窗口自动继承）；返回是否挂上。

    调用方在建好 QApplication、创建任何窗口之前调用。
    """
    path = icon_path()
    if path is None:
        return False
    from PyQt6.QtGui import QIcon

    icon = QIcon(str(path))
    if icon.isNull():
        return False
    app.setWindowIcon(icon)
    return True


def set_linux_desktop_file_name() -> None:
    """让 Linux 桌面按 ``lvjiang.desktop`` 匹配窗口与图标。

    Wayland 下客户端无法设置顶层窗口图标，compositor 只按窗口 app_id 查找同名
    桌面入口；Qt 默认取可执行文件名，显式声明后与随包安装的入口保持一致。
    """
    if not sys.platform.startswith("linux"):
        return
    from PyQt6.QtGui import QGuiApplication

    QGuiApplication.setDesktopFileName(DESKTOP_FILE_NAME)


def set_windows_app_user_model_id() -> None:
    """让 Windows 任务栏使用本程序的图标与分组。

    必须在创建第一个窗口之前设置；非 Windows 或接口不可用时静默跳过。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # 老系统或受限环境没有该接口：只是任务栏图标回退，不影响功能。
        pass
