"""常量定义"""

from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# 系统配置（随版本发布）
SYSTEM_CONFIG_DIR = CONFIG_DIR / "system"
SYSTEM_SCENES_DIR = SYSTEM_CONFIG_DIR / "scenes"
SYSTEM_WORKFLOWS_DIR = SYSTEM_CONFIG_DIR / "workflows"
APP_CONFIG_PATH = SYSTEM_CONFIG_DIR / "app.yaml"

# 本地数据（运行时生成，.gitignore）
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"
PREFERENCES_PATH = LOCAL_CONFIG_DIR / "preferences.yaml"
SESSION_PATH = LOCAL_CONFIG_DIR / "session.json"

# 装备部位枚举值
EQUIP_SLOT_NAMES = {
    "main_weapon": "主武器",
    "sub_weapon": "副武器",
    "head": "冠胄",
    "chest": "胸甲",
    "leg": "胫甲",
    "wrist": "腕甲",
    "ring": "环",
    "pendant": "佩",
}

# 延迟参数已统一到 config.py 的 DelayConfig，不再在此处定义默认值
