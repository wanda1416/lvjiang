"""批处理配置管理 — 批量执行条目持久化

存储于 session.json 的 "batch" 节点，read-modify-write 模式。
每个 BatchEntry 描述一次 (账号, 角色) 执行轮次。
"""

import json
from dataclasses import dataclass, field

from loguru import logger

from ..constants import SESSION_CONFIG_DIR, SESSION_PATH

# ─── 数据类 ──────────────────────────────────────────────


@dataclass
class BatchEntry:
    """单条批处理条目：一个账号下的一个角色"""
    account: str        # 账号全称（显示 + 同账号检测）
    phone_tail: str     # 手机尾号（OCR 匹配目标，如 "1234"）
    role: str           # 角色名（显示用）
    role_index: int     # 角色下标 1-4（role_list panel 行号）
    enabled: bool = True  # 是否启用（配置页勾选）

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "phone_tail": self.phone_tail,
            "role": self.role,
            "role_index": self.role_index,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchEntry":
        return BatchEntry(
            account=d.get("account", ""),
            phone_tail=d.get("phone_tail", ""),
            role=d.get("role", ""),
            role_index=int(d.get("role_index", 1)),
            enabled=d.get("enabled", True),
        )


@dataclass
class BatchConfig:
    """批处理配置：条目列表 + 勾选的脚本 ID 列表"""
    entries: list[BatchEntry] = field(default_factory=list)
    script_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "script_ids": list(self.script_ids),
        }

    @staticmethod
    def from_dict(d: dict) -> "BatchConfig":
        return BatchConfig(
            entries=[BatchEntry.from_dict(e) for e in d.get("entries", [])],
            script_ids=list(d.get("script_ids", [])),
        )


# ─── 持久化 ──────────────────────────────────────────────


def load_batch_config() -> BatchConfig:
    """从 session.json 读取批处理配置"""
    if not SESSION_PATH.exists():
        return BatchConfig()
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        return BatchConfig.from_dict(data.get("batch", {}))
    except Exception as e:
        logger.error(f"加载批处理配置失败: {e}")
        return BatchConfig()


def save_batch_config(cfg: BatchConfig) -> None:
    """保存批处理配置到 session.json（read-modify-write）"""
    data: dict = {}
    if SESSION_PATH.exists():
        try:
            data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["batch"] = cfg.to_dict()
    SESSION_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"批处理配置已保存: {len(cfg.entries)} 条条目")
