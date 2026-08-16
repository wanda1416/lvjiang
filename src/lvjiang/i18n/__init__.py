"""国际化支持（i18n）—— 翻译加载与查询

翻译文件分层加载：
  - 主体：config/i18n/{lang}.yaml（启动时加载）
  - 插件：config/i18n/apps/{app_name}/{lang}.yaml（插件加载时按需加载）
tr(text) 以中文原文为 key 查找翻译，找不到则返回原文。
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ..constants import PROJECT_ROOT

logger = logging.getLogger(__name__)

# ── 全局状态 ──
_translations: dict[str, str] = {}   # 扁平化翻译表：中文原文 → 译文
_current_language: str = "zh_CN"
_i18n_dir: Path = PROJECT_ROOT / "config" / "i18n"
_loaded_app_i18n: set[str] = set()   # 已加载插件翻译的集合（幂等保护）


def init_i18n(language: str | None = None) -> str:
    """初始化 i18n，加载翻译文件。

    Args:
        language: 语言代码（如 "zh_CN"、"en_US"），None 表示读取用户配置。

    Returns:
        实际加载的语言代码。
    """
    global _current_language, _translations, _loaded_app_i18n

    if language is None or language == "auto":
        language = _detect_system_language() if language == "auto" else _load_config_language()

    _current_language = language
    _translations = _load_translation_file(language)
    _loaded_app_i18n.clear()  # 重置后需重新加载插件翻译

    if language != "zh_CN":
        logger.info("[i18n] 已加载语言: %s，共 %d 条翻译", language, len(_translations))
    return language


def tr(text: str) -> str:
    """查找翻译。

    以中文原文 text 为 key，在当前翻译表中查找对应值。
    找不到时返回原文（中文 fallback）。
    zh_CN 模式下直接返回原文，零开销。
    """
    if _current_language == "zh_CN":
        return text
    return _translations.get(text, text)


def load_app_i18n(app_name: str) -> None:
    """加载插件的翻译文件，合并到全局翻译表。

    在 init_i18n() 之后调用，仅当语言非 zh_CN 时实际加载。
    插件翻译文件位于 config/i18n/apps/{app_name}/{lang}.yaml。
    同一插件重复调用时幂等跳过。
    """
    global _translations

    if _current_language == "zh_CN":
        return
    if app_name in _loaded_app_i18n:
        return

    app_dir = _i18n_dir / "apps" / app_name
    if not app_dir.is_dir():
        return

    yaml_file = app_dir / f"{_current_language}.yaml"
    if not yaml_file.exists():
        return

    zh_file = app_dir / "zh_CN.yaml"

    try:
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        logger.exception("[i18n] 解析插件翻译文件失败: %s", yaml_file)
        return

    zh_data: dict = {}
    if zh_file.exists():
        try:
            with open(zh_file, "r", encoding="utf-8") as f:
                zh_data = yaml.safe_load(f) or {}
        except Exception:
            logger.warning("[i18n] 解析插件中文基准文件失败: %s", zh_file)

    before = len(_translations)
    _build_translation_map(zh_data, data, _translations)
    added = len(_translations) - before
    _loaded_app_i18n.add(app_name)
    if added:
        logger.info("[i18n] 已加载插件 %s 翻译: %d 条", app_name, added)


def set_language(language: str) -> None:
    """切换语言（需重启生效）。

    更新 session.json 中的 language 配置，下次启动时生效。
    """
    from ..core.config import save_settings
    save_settings({"language": language})


def current_language() -> str:
    """返回当前语言代码。"""
    return _current_language


def available_languages() -> list[dict[str, str]]:
    """扫描 config/i18n/ 目录，返回可用语言列表。

    Returns:
        [{"code": "zh_CN", "name": "简体中文"}, ...]
    """
    languages = []
    if not _i18n_dir.exists():
        return [{"code": "zh_CN", "name": "简体中文"}]

    for yaml_file in sorted(_i18n_dir.glob("*.yaml")):
        code = yaml_file.stem
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            display_name = data.get("meta", {}).get("display_name", code)
            languages.append({"code": code, "name": display_name})
        except Exception:
            logger.warning("[i18n] 加载语言文件失败: %s", yaml_file)

    return languages or [{"code": "zh_CN", "name": "简体中文"}]


# ── 内部函数 ──

def _load_translation_file(language: str) -> dict[str, str]:
    """加载指定语言的翻译文件，扁平化为 {原文: 译文} 字典。

    同时加载 zh_CN.yaml 基准文件，与目标语言文件并行递归遍历，
    建立 中文原文 → 译文 的映射。结构不对齐的条目自动跳过。
    """
    yaml_file = _i18n_dir / f"{language}.yaml"
    if not yaml_file.exists():
        logger.warning("[i18n] 翻译文件不存在: %s", yaml_file)
        return {}

    try:
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        logger.exception("[i18n] 解析翻译文件失败: %s", yaml_file)
        return {}

    # 加载中文基准文件，用于建立 原文 → 译文 映射
    zh_file = _i18n_dir / "zh_CN.yaml"
    zh_data: dict = {}
    if zh_file.exists() and language != "zh_CN":
        try:
            with open(zh_file, "r", encoding="utf-8") as f:
                zh_data = yaml.safe_load(f) or {}
        except Exception:
            pass

    result: dict[str, str] = {}
    _build_translation_map(zh_data, data, result)
    return result


def _build_translation_map(zh_data: dict, trans_data: dict, result: dict) -> None:
    """递归遍历 zh_data 和 trans_data，建立 中文值 → 翻译值 的映射。

    两个 dict 并行递归：zh_data 取叶子值作为 key，trans_data 同路径叶子值作为 value。
    结构不对齐时（如 en_US.yaml 缺少某个节点），该分支自动跳过。
    """
    for key, trans_value in trans_data.items():
        if key == "meta":
            continue
        zh_value = zh_data.get(key) if isinstance(zh_data, dict) else None

        if isinstance(trans_value, dict) and isinstance(zh_value, dict):
            _build_translation_map(zh_value, trans_value, result)
        elif isinstance(zh_value, str) and isinstance(trans_value, str):
            result[zh_value] = trans_value
        # 结构不对齐（zh_value 为 None 或类型不匹配）→ 静默跳过


def _detect_system_language() -> str:
    """检测系统语言。"""
    try:
        from PyQt6.QtCore import QLocale
        name = QLocale.system().name()  # 如 "en_US"
        if name:
            return name
    except Exception:
        pass
    return "zh_CN"


def _load_config_language() -> str:
    """从 session.json 读取语言配置。"""
    try:
        from ..core.config import get_session_store
        settings = get_session_store().get_node("settings", {})
        return settings.get("language", "zh_CN")
    except Exception:
        return "zh_CN"
