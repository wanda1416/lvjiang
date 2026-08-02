"""OCR 文本通用清洗器

配置合并：system < local
- config/system/ocr_rules.yaml  系统默认规则（随代码分发）
- config/local/ocr_rules.yaml   用户自定义规则（覆盖系统默认）

规则类型：
- replacements: 文本替换 {"错误文本": "正确文本"} 或 {"噪声": ""}
- patterns: 正则替换 {"正则": "替换"}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# 配置文件路径
_SYSTEM_CONFIG = Path("config/system/ocr_rules.yaml")
_LOCAL_CONFIG = Path("config/local/ocr_rules.yaml")


class OCRCleaner:
    """OCR 文本通用清洗器（单例）"""

    _instance: OCRCleaner | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self):
        """加载并合并配置：system < local"""
        self._config = {}

        # 1. 系统默认配置
        if _SYSTEM_CONFIG.exists():
            try:
                cfg = yaml.safe_load(_SYSTEM_CONFIG.read_text(encoding="utf-8"))
                if cfg:
                    self._config = cfg
            except Exception as e:
                logger.warning(f"加载系统 OCR 清洗配置失败: {e}")

        # 2. 用户本地配置（覆盖）
        if _LOCAL_CONFIG.exists():
            try:
                local_cfg = yaml.safe_load(_LOCAL_CONFIG.read_text(encoding="utf-8"))
                if local_cfg:
                    self._merge_config(local_cfg)
            except Exception as e:
                logger.warning(f"加载本地 OCR 清洗配置失败: {e}")

        logger.debug(f"OCR 清洗器加载完成: {len(self._config.get('replacements', {}))} 条替换规则, "
                     f"{len(self._config.get('patterns', {}))} 条正则规则")

    def _merge_config(self, local: dict[str, Any]):
        """合并本地配置到当前配置"""
        # replacements: 直接合并（本地覆盖系统）
        if "replacements" in local:
            self._config.setdefault("replacements", {}).update(local["replacements"])

        # patterns: 直接合并（本地覆盖系统）
        if "patterns" in local:
            self._config.setdefault("patterns", {}).update(local["patterns"])

    def reload(self):
        """重新加载配置"""
        self._load_config()

    def clean(self, text: str) -> str:
        """清洗 OCR 文本

        按顺序执行：
        1. 文本替换（replacements）
        2. 正则替换（patterns）
        3. 首尾空白去除

        Args:
            text: 原始 OCR 文本

        Returns:
            清洗后的文本
        """
        if not text:
            return text

        result = text

        # 1. 文本替换
        for wrong, correct in self._config.get("replacements", {}).items():
            result = result.replace(wrong, correct)

        # 2. 正则替换
        for pattern, replacement in self._config.get("patterns", {}).items():
            if pattern:
                try:
                    result = re.sub(pattern, replacement, result)
                except re.error as e:
                    logger.warning(f"正则替换失败 '{pattern}': {e}")

        return result.strip()

    # ─── 配置管理（供 UI 调用）───────────────────────────────

    def get_replacements(self) -> dict[str, str]:
        """获取所有文本替换规则"""
        return dict(self._config.get("replacements", {}))

    def get_patterns(self) -> dict[str, str]:
        """获取所有正则替换规则"""
        return dict(self._config.get("patterns", {}))

    def add_replacement(self, wrong: str, correct: str):
        """添加文本替换规则并保存到本地配置"""
        self._config.setdefault("replacements", {})[wrong] = correct
        self._save_local_config()

    def remove_replacement(self, wrong: str):
        """删除文本替换规则并保存到本地配置"""
        if wrong in self._config.get("replacements", {}):
            del self._config["replacements"][wrong]
            self._save_local_config()

    def set_replacements(self, replacements: dict[str, str]):
        """批量设置文本替换规则并保存一次"""
        self._config["replacements"] = dict(replacements)
        self._save_local_config()

    def add_pattern(self, pattern: str, replacement: str):
        """添加正则替换规则并保存到本地配置"""
        self._config.setdefault("patterns", {})[pattern] = replacement
        self._save_local_config()

    def remove_pattern(self, pattern: str):
        """删除正则替换规则并保存到本地配置"""
        patterns = self._config.get("patterns", {})
        if pattern in patterns:
            del patterns[pattern]
            self._save_local_config()

    def set_patterns(self, patterns: dict[str, str]):
        """批量设置正则替换规则并保存一次"""
        self._config["patterns"] = dict(patterns)
        self._save_local_config()

    def _save_local_config(self):
        """保存当前配置到本地文件"""
        _LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)

        local_cfg: dict[str, Any] = {}

        replacements = self._config.get("replacements", {})
        if replacements:
            local_cfg["replacements"] = replacements

        patterns = self._config.get("patterns", {})
        if patterns:
            local_cfg["patterns"] = patterns

        try:
            _LOCAL_CONFIG.write_text(
                yaml.dump(local_cfg, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            logger.debug(f"OCR 清洗配置已保存: {_LOCAL_CONFIG}")
        except Exception as e:
            logger.error(f"保存 OCR 清洗配置失败: {e}")

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None
