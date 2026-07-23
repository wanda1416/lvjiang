"""通用常量定义。

燕云专属常量（如 ``EQUIP_SLOT_NAMES``）请放到 ``src.apps.yysls.constants``。
"""

from pathlib import Path

# 项目根目录（src/ 的父级）
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# 系统配置（随版本发布）
SYSTEM_CONFIG_DIR = CONFIG_DIR / "system"
SYSTEM_SCENES_DIR = SYSTEM_CONFIG_DIR / "scenes"
SYSTEM_WORKFLOWS_DIR = SYSTEM_CONFIG_DIR / "workflows"
SYSTEM_RULES_DIR = SYSTEM_CONFIG_DIR / "rules"
SCENES_CONFIG_PATH = SYSTEM_CONFIG_DIR / "scenes.yaml"

# 本地数据（运行时生成，.gitignore）
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"
PREFERENCES_PATH = LOCAL_CONFIG_DIR / "preferences.yaml"
SESSION_PATH = LOCAL_CONFIG_DIR / "session.json"
USERS_DIR = LOCAL_CONFIG_DIR / "users"
OUTPUT_DIR = LOCAL_CONFIG_DIR / "output"

# 延迟参数已统一到 config.py 的 DelayConfig，不再在此处定义默认值
