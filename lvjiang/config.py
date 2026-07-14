"""配置加载与 Pydantic 校验"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .constants import DEFAULT_CONFIG_PATH, BASE_RESOLUTION


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
    """用户配置"""
    target_flow: str = "会心双刀"
    auto_protect_top_tier: bool = True
    keep_strategy: str = "top2"
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    delay: DelayConfig = Field(default_factory=DelayConfig)
    window_title: str = ""


class RegionConfig(BaseModel):
    """单个区域配置"""
    type: str  # box / point / grid
    # box 类型字段
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    # point 类型字段
    x: int = 0
    y: int = 0
    # grid 类型字段
    first_cell: list[int] = Field(default_factory=lambda: [0, 0])
    cell_size: list[int] = Field(default_factory=lambda: [80, 80])
    gap: list[int] = Field(default_factory=lambda: [10, 10])
    cols: int = 6
    rows: int = 5
    # 可选颜色校验
    color_check: list[int] | None = None


class CoordinateConfig(BaseModel):
    """坐标配置文件"""
    base_resolution: list[int] = Field(default_factory=lambda: list(BASE_RESOLUTION))
    regions: dict[str, RegionConfig] = Field(default_factory=dict)


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
    """加载用户配置"""
    config_path = path or DEFAULT_CONFIG_PATH
    data = load_yaml(config_path)
    return UserConfig(**data)


def load_coordinate_config(path: Path | None = None) -> CoordinateConfig:
    """加载坐标配置"""
    from .constants import COORDINATE_CONFIG_PATH
    config_path = path or COORDINATE_CONFIG_PATH
    data = load_yaml(config_path)
    return CoordinateConfig(**data)
