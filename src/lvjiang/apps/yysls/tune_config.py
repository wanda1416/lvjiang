"""调律配置 —— session.json tuning 节的类型化读写

将散落在各处的 raw dict 访问收口为 dataclass，提供字段补全与
IDE 重命名支持。与 game_config（attributes.yaml 管理器）对称，
同属 yysls 插件的两大配置入口。

数据来源：config/session/yysls/session.json → "tuning" 节点
"""

from __future__ import annotations

from dataclasses import dataclass, field

_SECTION_KEY = "tuning"


@dataclass
class TuneConfig:
    """调律配置（对应 session.json 的 tuning 节）

    selected_slots:  勾选的调律部位 key 列表
    rules:           规则配置 {规则 key: {"enabled": bool, "playstyles": [...]}}
    switches:        全局开关 {开关 key: bool}
    skip_tuning:     跳过实际调律（仅模拟进出调律页，测试用）
    skip_start:      初始跳过 (row, col)；None 表示不跳过
    target_cell:     指定调律 (row, col)；None 表示不指定
    scroll_strategy: 背包遍历策略 key（空=读默认）
    """

    selected_slots: list[str] = field(default_factory=list)
    rules: dict[str, dict] = field(default_factory=dict)
    switches: dict[str, bool] = field(default_factory=dict)
    skip_tuning: bool = False
    skip_start: list[int] | None = None
    target_cell: list[int] | None = None
    scroll_strategy: str = ""

    # ─── 持久化 ──────────────────────────────────────

    @classmethod
    def load(cls) -> TuneConfig:
        """从插件会话加载调律配置"""
        from .session import get_plugin_session
        data = get_plugin_session().get_section(_SECTION_KEY)
        raw_rules = data.get("rules")
        return cls(
            selected_slots=data.get("selected_slots") or [],
            rules=raw_rules if isinstance(raw_rules, dict) else {},
            switches=data.get("switches") or {},
            skip_tuning=bool(data.get("skip_tuning", False)),
            skip_start=data.get("skip_start"),
            target_cell=data.get("target_cell"),
            scroll_strategy=data.get("scroll_strategy") or "",
        )

    def save(self) -> None:
        """写入插件会话并落盘"""
        from .session import get_plugin_session
        get_plugin_session().set_section(_SECTION_KEY, {
            "selected_slots": self.selected_slots,
            "rules": self.rules,
            "switches": self.switches,
            "skip_tuning": self.skip_tuning,
            "skip_start": self.skip_start,
            "target_cell": self.target_cell,
            "scroll_strategy": self.scroll_strategy,
        })
        # 同步单例，防止 get_tune_config() 返回过期数据
        global _instance
        _instance = self


_instance: TuneConfig | None = None


def get_tune_config() -> TuneConfig:
    """获取全局 TuneConfig 单例"""
    global _instance
    if _instance is None:
        _instance = TuneConfig.load()
    return _instance
