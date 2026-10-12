"""客户端目录关系的唯一声明处，不导入配置运行时。"""
from pathlib import Path

from .. import constants

# 声明相对目录而非 import 时解析的绝对目录；集中修改挂载位置也由此完成。
CONFIG_SUBDIRECTORIES: dict[str, Path] = {
    "system": Path("system"),
    "local": Path("local"),
    "updates": Path("updates"),
    "i18n": Path("i18n"),
}


def config_directory(root: Path | None = None) -> Path:
    """客户端默认配置根，或显式安装实例的配置根。"""
    if root is None or root.resolve() == constants.PROJECT_ROOT.resolve():
        return constants.CONFIG_DIR
    return root / "config"


def _config_directory(config: Path | None, name: str) -> Path:
    if config is None or config.resolve() == constants.CONFIG_DIR.resolve():
        return constants.CONFIG_DIR / CONFIG_SUBDIRECTORIES[name]
    # 显式隔离实例不继承默认实例的绝对挂载位置。
    return config / name


def get_config_system_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "system")


def get_config_local_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "local")


def get_config_updates_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "updates")


def get_config_i18n_dir(config: Path | None = None) -> Path:
    return _config_directory(config, "i18n")


def data_directory(root: Path | None = None) -> Path:
    if root is None or root.resolve() == constants.PROJECT_ROOT.resolve():
        return constants.DATA_DIR
    return root / "data"


def session_directory(config: Path | None = None) -> Path:
    """默认使用客户端 session；隔离实例、打包和手机同步可指定配置根。"""
    if config is None or config.resolve() == constants.CONFIG_DIR.resolve():
        return constants.SESSION_CONFIG_DIR
    return config / "session"
