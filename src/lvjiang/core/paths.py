"""客户端及显式配置根的运行状态路径，不导入配置运行时。"""
from pathlib import Path

from .. import constants


def session_directory(config: Path | None = None) -> Path:
    """默认使用客户端 session；隔离实例、打包和手机同步可指定配置根。"""
    if config is None or config.resolve() == constants.CONFIG_DIR.resolve():
        return constants.SESSION_CONFIG_DIR
    return config / "session"
