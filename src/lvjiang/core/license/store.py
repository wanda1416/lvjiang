"""激活码存放 — 用户侧，不随包分发

放 ``config/local/license.txt``：

- **不能放 config/system**。随包分发的那一层在升级时会被整个清空（安装器的
  ``[InstallDelete]``、安卓解压前的删除），放那儿升级一次授权就没了。
- 也不放 ``config/session``：那是运行数据（账号、历史、产出），语义上不是用户配置。

存的是激活码本身，不是「已激活」的布尔值——每次启动重新验签，改文件伪造不出授权。
序列号则始终现算、不落盘。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_FILENAME = "license.txt"


def license_path() -> Path:
    from ..config.resolver import LOCAL_CONFIG_DIR
    return LOCAL_CONFIG_DIR / _FILENAME


def load_code() -> str | None:
    """读取已保存的激活码；没有或读不出返回 ``None``"""
    path = license_path()
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning(f"读取激活码失败: {exc}")
        return None
    return text or None


def save_code(code: str) -> bool:
    """保存激活码，成功返回 True"""
    path = license_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code.strip() + "\n", encoding="utf-8")
    except OSError as exc:
        logger.error(f"保存激活码失败: {exc}")
        return False
    logger.info(f"激活码已保存: {path}")
    return True


def clear_code() -> bool:
    """删除已保存的激活码（取消激活）"""
    path = license_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error(f"删除激活码失败: {exc}")
        return False
    return True
