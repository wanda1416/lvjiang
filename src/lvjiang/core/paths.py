"""客户端目录关系的唯一声明处，不导入配置运行时。"""
from pathlib import Path

from .. import constants

SYSTEM_CONFIG_DIR = constants.CONFIG_DIR / "system"
LOCAL_CONFIG_DIR = constants.CONFIG_DIR / "local"
UPDATES_CONFIG_DIR = constants.CONFIG_DIR / "updates"
I18N_CONFIG_DIR = constants.CONFIG_DIR / "i18n"


def config_directory(root: Path | None = None) -> Path:
    """客户端默认配置根，或显式安装实例的配置根。"""
    if root is None or root.resolve() == constants.PROJECT_ROOT.resolve():
        return constants.CONFIG_DIR
    return root / "config"


def _config_directory(config: Path | None, name: str, default: Path) -> Path:
    if config is None or config.resolve() == constants.CONFIG_DIR.resolve():
        return default
    return config / name


def get_config_system_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "system", SYSTEM_CONFIG_DIR)


def get_config_local_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "local", LOCAL_CONFIG_DIR)


def get_config_updates_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "updates", UPDATES_CONFIG_DIR)


def get_config_i18n_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "i18n", I18N_CONFIG_DIR)


def data_directory(root: Path | None = None) -> Path:
    if root is None or root.resolve() == constants.PROJECT_ROOT.resolve():
        return constants.DATA_DIR
    return root / "data"


def session_directory(config: Path | None = None) -> Path:
    """默认使用客户端 session；隔离实例、打包和手机同步可指定配置根。"""
    return _config_directory(config, "session", constants.SESSION_CONFIG_DIR)
