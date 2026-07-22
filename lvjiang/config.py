"""配置加载与 Pydantic 校验"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .constants import SCENES_CONFIG_PATH, PREFERENCES_PATH


class DelayConfig(BaseModel):
    """延迟参数（模拟人类操作）"""
    # ── 底层点击（InputBackend 各子类使用）──
    before_click_wait: tuple[float, float] = (0.1, 0.3)   # 点击前延迟范围（模拟反应时间）
    after_click_wait: tuple[float, float] = (0.1, 0.2)    # 点击后延迟范围
    mouse_move_duration: tuple[float, float] = (0.3, 0.6) # 鼠标移动时长范围
    click_random_offset: int = 3                           # 坐标随机偏移像素
    region_jitter_ratio: float = Field(default=0.25, ge=0, lt=0.5)  # 区域中心(0.5)左右偏移比例，必须 [0, 0.5)

    # ── 工作流级等待 ──
    step_interval: tuple[float, float] = (1.5, 2.5)       # 步骤间等待
    click_interval: tuple[float, float] = (1.5, 2.5)      # 连续点击间隔（未来扩展）
    page_refresh_wait: float | tuple[float, float] = 2.0   # 点击后页面刷新等待（单值固定等待，二元组则范围内随机）
    scroll_settle_wait: float | tuple[float, float] = 3.0  # 滚动拖拽后惯性停止等待（必须等列表彻底停下再读取）
    after_tune_wait: float | tuple[float, float] = 3.0     # 调律结果等待（单值固定等待，二元组则范围内随机）


class MaterialGridConfig(BaseModel):
    """材料网格切割默认参数"""
    rows: int = Field(default=3, ge=1)     # 默认行数
    cols: int = Field(default=6, ge=1)     # 默认列数
    gap: int = Field(default=0, ge=0)      # 默认间隔(px)
    height: int = Field(default=100, ge=1) # 默认单cell高度(px)
    width: int = Field(default=100, ge=1)  # 默认单cell宽度(px)


class UserConfig(BaseModel):
    """用户配置（从 preferences.yaml 加载，只读）"""
    adb_capture_streaming: bool = False    # ADB 模式是否启用 scrcpy 视频流截图（false 则用 screencap）
    desktop_window_title: str = ""         # 桌面模式投屏窗口标题关键字
    desktop_background_input: bool = True  # 桌面模式是否启用后台输入（PostMessage）
    material_grid: MaterialGridConfig = Field(default_factory=MaterialGridConfig)
    input_delay: DelayConfig = Field(default_factory=DelayConfig)


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
    """加载用户配置（scenes.yaml 默认值 + preferences.yaml 覆盖）"""
    if path:
        data = load_yaml(path)
    else:
        # 先加载系统默认，再用用户偏好覆盖
        data = load_yaml(SCENES_CONFIG_PATH)
        prefs = load_yaml(PREFERENCES_PATH)
        data.update(prefs)
    return UserConfig(**data)
