"""基础属性配置存储（兼容旧的 play_styles 命名）。

基础属性数据属于会话级数据（由面板属性反推），不应提交到 git。
存储在 config/session/yysls.json：

    play_styles: {
        流派名: {
            基础属性名称: { field_name: value, ... }
        }
    }

此文件与用户无关，所有用户共享同一套基础属性配置。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR

# 基础属性配置文件路径
_PLAY_STYLES_PATH = SESSION_CONFIG_DIR / "yysls.json"


def _load() -> dict:
    """加载配置文件"""
    if not _PLAY_STYLES_PATH.exists():
        return {}
    try:
        return json.loads(_PLAY_STYLES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"加载基础属性配置失败: {e}")
        return {}


def _save(data: dict) -> None:
    """保存配置文件"""
    _PLAY_STYLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(
        dir=str(_PLAY_STYLES_PATH.parent),
        prefix=f".{_PLAY_STYLES_PATH.stem}_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, _PLAY_STYLES_PATH)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def get_play_styles(school: str) -> dict[str, dict]:
    """获取指定流派的全部基础属性配置。

    Args:
        school: 流派名称

    Returns:
        基础属性字典：名称 → {field_name: value, ...}
    """
    data = _load()
    all_styles = data.get("play_styles", {})
    return dict(all_styles.get(school) or {})


def save_play_style(school: str, name: str, attrs: dict) -> None:
    """保存一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
        attrs: 属性字典 {field_name: value}
    """
    data = _load()
    all_styles = data.setdefault("play_styles", {})
    school_styles = all_styles.setdefault(school, {})
    school_styles[name] = attrs
    _save(data)
    logger.debug(f"已保存基础属性: {school}/{name}")


def delete_play_style(school: str, name: str) -> None:
    """删除一套基础属性。

    Args:
        school: 流派名称
        name: 基础属性名称
    """
    data = _load()
    all_styles = data.get("play_styles", {})
    school_styles = all_styles.get(school, {})
    school_styles.pop(name, None)
    _save(data)
    logger.debug(f"已删除基础属性: {school}/{name}")


def rename_play_style(school: str, old_name: str, new_name: str) -> None:
    """重命名一套基础属性。

    Args:
        school: 流派名称
        old_name: 旧名称
        new_name: 新名称
    """
    data = _load()
    all_styles = data.get("play_styles", {})
    school_styles = all_styles.get(school, {})
    if old_name in school_styles:
        school_styles[new_name] = school_styles.pop(old_name)
    _save(data)
    logger.debug(f"已重命名基础属性: {school}/{old_name} → {new_name}")
