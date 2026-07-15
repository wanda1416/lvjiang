"""常量定义"""

from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# 系统配置（随版本发布）
SYSTEM_CONFIG_DIR = CONFIG_DIR / "system"
SYSTEM_SCENES_DIR = SYSTEM_CONFIG_DIR / "scenes"
APP_CONFIG_PATH = SYSTEM_CONFIG_DIR / "app.yaml"

# 本地数据（运行时生成，.gitignore）
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"
PREFERENCES_PATH = LOCAL_CONFIG_DIR / "preferences.yaml"
SESSION_PATH = LOCAL_CONFIG_DIR / "session.json"

# 基准分辨率（坐标配置基于此分辨率）
BASE_RESOLUTION = (1920, 1080)

# 装备部位枚举值
EQUIP_SLOTS = ["weapon", "head", "chest", "leg", "wrist", "ring", "pendant"]
EQUIP_SLOT_NAMES = {
    "weapon": "武器",
    "head": "头部",
    "chest": "胸部",
    "leg": "腿部",
    "wrist": "腕部",
    "ring": "环",
    "pendant": "佩",
}

# 默认延迟参数（秒）
DEFAULT_CLICK_INTERVAL = (0.1, 0.3)
DEFAULT_AFTER_CLICK_WAIT = (0.1, 0.2)
DEFAULT_AFTER_TUNE_WAIT = 1.5

# 鼠标随机偏移范围（像素）
CLICK_RANDOM_OFFSET = 3
