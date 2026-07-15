"""配置加载与 Pydantic 校验"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .constants import APP_CONFIG_PATH, PREFERENCES_PATH


class DelayConfig(BaseModel):
    """延迟参数"""
    click_interval: tuple[float, float] = (0.1, 0.3)
    after_click_wait: tuple[float, float] = (0.1, 0.2)
    after_tune_wait: float = 1.5


class BudgetConfig(BaseModel):
    """调律预算"""
    max_tunes_per_equip: int = 20
    material_threshold: int = 10


class UserConfig(BaseModel):
    """用户配置（从 preferences.yaml 加载）"""
    window_title: str = ""
    target_flow: str = "会心双刀"
    auto_protect_top_tier: bool = True
    keep_strategy: str = "top2"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    delay: DelayConfig = Field(default_factory=DelayConfig)


def load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data else {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """保存 YAML 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_user_config(path: Path | None = None) -> UserConfig:
    """加载用户配置（app.yaml 默认值 + preferences.yaml 覆盖）"""
    if path:
        data = load_yaml(path)
    else:
        # 先加载系统默认，再用用户偏好覆盖
        data = load_yaml(APP_CONFIG_PATH)
        prefs = load_yaml(PREFERENCES_PATH)
        data.update(prefs)
    return UserConfig(**data)
