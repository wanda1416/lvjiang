"""OCR 文本通用清洗器

配置通过 ConfigResolver 双层合并读写：
- config/system/ocr_rules.yaml  系统默认规则（随代码分发）
- config/local/ocr_rules.yaml   用户自定义规则（覆盖系统默认）
开发模式写入 system，用户模式写入 local diff。

规则类型：
- replacements: 文本替换 {"错误文本": "正确文本"} 或 {"噪声": ""}
- patterns: 正则替换 {"正则": "替换"}
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

# 配置文件相对路径（相对于 config 层根）
_REL_PATH = "ocr_rules.yaml"


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
        """通过 ConfigResolver 加载并合并配置：system < local"""
        from .config import get_resolver
        self._config = get_resolver().load_merged(_REL_PATH)
        logger.debug(f"OCR 清洗器加载完成: {len(self._config.get('replacements', {}))} 条替换规则, "
                     f"{len(self._config.get('patterns', {}))} 条正则规则")

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


    def set_replacements(self, replacements: dict[str, str]):
        """批量设置文本替换规则并保存一次"""
        self._config["replacements"] = dict(replacements)
        self._save_config()


    def set_patterns(self, patterns: dict[str, str]):
        """批量设置正则替换规则并保存一次"""
        self._config["patterns"] = dict(patterns)
        self._save_config()

    def _save_config(self):
        """通过 ConfigResolver 保存完整配置（开发→system，用户→local diff）

        必须保存 self._config 全量内容，而非仅已知键；
        否则开发模式下 save_merged 全量写 system 会丢失未来新增的键。
        """
        from .config import get_resolver
        doc: dict[str, Any] = {k: v for k, v in self._config.items() if v is not None}
        try:
            get_resolver().save_merged(_REL_PATH, doc)
        except Exception as e:
            logger.error(f"保存 OCR 清洗配置失败: {e}")

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None
