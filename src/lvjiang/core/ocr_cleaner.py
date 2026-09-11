"""按业务规则组执行 OCR 文本清洗。

配置通过 ConfigResolver 多层合并读写：
- config/system/ocr.yaml  系统默认配置（随代码分发）
- config/remote/ocr.yaml  在线更新配置（版本较新时生效）
- config/local/ocr.yaml   用户自定义配置（覆盖前两层）
开发模式写入 system，用户模式写入 local diff。

每个 ``normalization.groups.<key>`` 包含展示用 ``label`` 以及两类规则：
- replacements: 文本替换 {"错误文本": "正确文本"} 或 {"噪声": ""}
- patterns: 正则替换 {"正则": "替换"}
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from .ocr_config import OCR_CONFIG_PATH, load_ocr_config


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
        """通过 ConfigResolver 加载并合并配置。"""
        self._config = load_ocr_config()
        groups = self.get_groups()
        logger.debug(
            f"OCR 清洗器加载完成: "
            f"{len(groups)} 个规则组"
        )

    def _normalization(self) -> dict[str, Any]:
        value = self._config.get("normalization")
        return value if isinstance(value, dict) else {}

    def reload(self):
        """重新加载配置"""
        self._load_config()

    def get_groups(self) -> dict[str, dict[str, Any]]:
        """返回清洗组配置的浅拷贝，key 是供 DSL/API 使用的稳定标识。"""
        groups = self._normalization().get("groups")
        if not isinstance(groups, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in groups.items()
            if isinstance(value, dict)
        }

    def get_group_label(self, group: str) -> str:
        config = self._group(group)
        return str(config.get("label") or group)

    def validate_group(self, group: str | None) -> None:
        if group is not None:
            self._group(group)

    def add_group(self, key: str, label: str) -> None:
        """创建空规则组；key 是稳定引用，label 仅用于界面展示。"""
        key = key.strip()
        label = label.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", key):
            raise ValueError("清洗组 key 只能包含小写字母、数字、下划线和连字符")
        if not label:
            raise ValueError("清洗组名称不能为空")
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        if key in groups:
            raise ValueError(f"OCR 清洗组已存在: {key}")
        groups[key] = {
            "label": label,
            "replacements": {},
            "patterns": {},
        }
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def rename_group(self, key: str, label: str) -> None:
        """只修改展示名称，保持工作流引用的 key 稳定。"""
        label = label.strip()
        if not label:
            raise ValueError("清洗组名称不能为空")
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        config = dict(self._group(key))
        config["label"] = label
        groups[key] = config
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def delete_group(self, key: str) -> None:
        """删除规则组；调用方负责确认其工作流引用是否仍然需要。"""
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        if key not in groups:
            raise ValueError(f"OCR 清洗组不存在: {key}")
        del groups[key]
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def _group(self, group: str) -> dict[str, Any]:
        config = self.get_groups().get(group)
        if config is None:
            raise ValueError(f"OCR 清洗组不存在: {group}")
        return config

    def clean(self, text: str, group: str | None = None) -> str:
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

        if group is None:
            return text.strip()
        result = text

        # 1. 文本替换
        config = self._group(group)
        for wrong, correct in config.get("replacements", {}).items():
            result = result.replace(wrong, correct)

        # 2. 正则替换
        for pattern, replacement in config.get("patterns", {}).items():
            if pattern:
                try:
                    result = re.sub(pattern, replacement, result)
                except re.error as e:
                    logger.warning(f"正则替换失败 '{pattern}': {e}")

        return result.strip()

    # ─── 配置管理（供 UI 调用）───────────────────────────────

    def get_replacements(self, group: str = "equip") -> dict[str, str]:
        """获取所有文本替换规则"""
        return dict(self._group(group).get("replacements", {}))

    def get_patterns(self, group: str = "equip") -> dict[str, str]:
        """获取所有正则替换规则"""
        return dict(self._group(group).get("patterns", {}))


    def set_replacements(self, replacements: dict[str, str], group: str = "equip"):
        """批量设置文本替换规则并保存一次"""
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        config = dict(self._group(group))
        config["replacements"] = dict(replacements)
        groups[group] = config
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def set_patterns(self, patterns: dict[str, str], group: str = "equip"):
        """批量设置正则替换规则并保存一次"""
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        config = dict(self._group(group))
        config["patterns"] = dict(patterns)
        groups[group] = config
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def set_rules(
        self,
        replacements: dict[str, str],
        patterns: dict[str, str],
        group: str = "equip",
    ) -> None:
        """同时保存两类规范化规则。"""
        normalization = self._normalization()
        groups = dict(normalization.get("groups") or {})
        config = dict(self._group(group))
        config["replacements"] = dict(replacements)
        config["patterns"] = dict(patterns)
        groups[group] = config
        normalization["groups"] = groups
        self._config["normalization"] = normalization
        self._save_config()

    def _save_config(self):
        """通过 ConfigResolver 保存完整配置（开发→system，用户→local diff）

        必须保存 self._config 全量内容，而非仅已知键；
        否则开发模式下 save_merged 全量写 system 会丢失未来新增的键。
        """
        from .config import get_resolver
        doc: dict[str, Any] = {k: v for k, v in self._config.items() if v is not None}
        try:
            get_resolver().save_merged(OCR_CONFIG_PATH, doc)
        except Exception as e:
            logger.error(f"保存 OCR 清洗配置失败: {e}")

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None
