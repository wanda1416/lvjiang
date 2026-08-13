"""通用常量定义。

燕云专属常量（如 ``EQUIP_SLOT_NAMES``）请放到 ``lvjiang.apps.yysls.constants``。
"""

import os
import sys
from pathlib import Path


def _project_root() -> Path:
    """项目根目录（config/ data/ logs/ 等可写数据的总根）

    桌面端：仓库根（src/ 的父级；本文件位于 src/lvjiang/ 下，故上溯两层）。

    打包端（PyInstaller）：__file__ 落在 _internal/ 解包目录，不能当根用；
    根 = exe 所在目录（config/system、data/scrcpy 随包放在 exe 旁）。

    安卓端（Chaquopy）：__file__ 落在 APK 解压目录，只读且路径随版本变，
    不能当根用。AndroidPlatform 会把 HOME 指到应用 filesDir，故改用
    $HOME/lvjiang；系统配置由 App 启动时从 assets 解压到这里（见 App.kt）。

    LVJIANG_ROOT 环境变量可在各端显式覆盖（测试/多实例隔离用）。
    """
    override = os.environ.get("LVJIANG_ROOT")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):  # PyInstaller 打包：根 = exe 所在目录
        return Path(sys.executable).parent
    if hasattr(sys, "getandroidapilevel"):
        return Path(os.environ["HOME"]) / "lvjiang"
    return Path(__file__).parent.parent.parent


PROJECT_ROOT = _project_root()
CONFIG_DIR = PROJECT_ROOT / "config"

# 系统配置（出厂默认，随版本发布，进 git）
SYSTEM_CONFIG_DIR = CONFIG_DIR / "system"
SYSTEM_WORKFLOWS_DIR = SYSTEM_CONFIG_DIR / "workflows"
SYSTEM_LAYOUTS_DIR = SYSTEM_CONFIG_DIR / "layouts"

# 用户覆盖层（影子文件 + 键级 diff + 墓碑，目录镜像 system，.gitignore）
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"

# 纯运行态（会话/用户/产出/截图，.gitignore）
SESSION_CONFIG_DIR = CONFIG_DIR / "session"
SESSION_PATH = SESSION_CONFIG_DIR / "session.json"
USERS_DIR = SESSION_CONFIG_DIR / "users"
OUTPUT_DIR = SESSION_CONFIG_DIR / "output"

# 用户采集产出（录屏/截屏，与场景布局截图、工作流产出分开）
DATA_DIR = PROJECT_ROOT / "data"
VIDEO_DIR = DATA_DIR / "video"
PICTURE_DIR = DATA_DIR / "picture"

# 延迟参数与输入模拟参数已拆分到 config.py 的 delay_params / InputSimConfig，
# 由 app.yaml 统一加载，不再在此处定义默认值
