"""激活码存放 — 用户侧，不随包分发

放 ``config/local/license.txt``：

- **不能放 config/system**。随包分发的那一层在升级时会被整个清空（安装器的
  ``[InstallDelete]``、安卓解压前的删除），放那儿升级一次授权就没了。
- 也不放 ``config/session``：那是运行数据（账号、历史、产出），语义上不是用户配置。

存的是激活码本身，不是「已激活」的布尔值——每次启动重新验签，改文件伪造不出授权。
序列号则始终现算、不落盘。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from loguru import logger

from ..json_files import formatted_json
from ..license_files import ACTIVATION_FILENAME, LICENSE_FILENAME


def license_path() -> Path:
    from ..config.resolver import LOCAL_CONFIG_DIR
    return LOCAL_CONFIG_DIR / LICENSE_FILENAME


def load_code() -> str | None:
    """读取已保存的激活码；没有或读不出返回 ``None``"""
    activation = load_activation()
    if activation is not None:
        code = activation.get("code")
        return code if isinstance(code, str) else None
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
        _atomic_write(path, code.strip() + "\n")
        activation_path().unlink(missing_ok=True)
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
        activation_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.error(f"删除激活码失败: {exc}")
        return False
    return True


def activation_path() -> Path:
    return license_path().with_name(ACTIVATION_FILENAME)


def load_activation() -> dict | None:
    path = activation_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning("激活记录格式无效：{}；请在设置中清除激活后同机重试", path)
    except (OSError, ValueError) as error:
        logger.warning("读取激活记录失败：{}；请在设置中清除激活后同机重试：{}", path, error)
    # Existing but invalid activation record must not restore a stale legacy license.
    return {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".license-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_activation(code: str, receipt: dict) -> bool:
    try:
        _atomic_write(activation_path(), formatted_json({"code": code, "receipt": receipt}))
    except OSError:
        logger.error("激活记录保存失败，原认证保留，可同机重试")
        return False
    try:
        license_path().unlink(missing_ok=True)
    except OSError as error:
        logger.warning("激活已保存，但旧激活码清理失败：{}；请删除旧 license.txt：{}", license_path(), error)
    return True
