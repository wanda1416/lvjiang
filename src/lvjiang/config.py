"""配置加载与校验（dataclass 实现）

配置分层（后者覆盖前者）：
1. 代码默认值（dataclass 字段默认）
2. session.json 的 settings / material_grid 节点（纯运行态，配置管理 / 图库管理写入）
3. app.yaml 的 input_simulation / delay_params 节点（system 出厂默认 ← local 用户覆盖，
   随版本分发，见 core.config）

不用 pydantic：它的 v2 核心是 Rust 扩展（pydantic-core），安卓设备端（Chaquopy）
无法安装；本模块只需要默认值 + 少量范围校验 + 嵌套 dict 转换，__post_init__
足以覆盖，桌面端与设备端共用同一份实现。
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from loguru import logger


def _pair(value: Any) -> tuple[float, float]:
    """list/tuple → (float, float)（JSON 反序列化出 list，统一成 tuple）"""
    lo, hi = value
    return (float(lo), float(hi))


@dataclass
class DelayParam:
    """单个命名延迟参数（配置管理「等待参数」页维护，供 wait_delay / DSL wait 按 key 引用）"""
    label: str = ""                                 # 显示名称
    range: tuple[float, float] = (1.0, 1.0)         # 等待范围（秒）

    def __post_init__(self):
        self.range = _pair(self.range)


@dataclass
class InputSimConfig:
    """输入模拟参数（引擎级点击/移动/抖动，InputBackend 各子类与引擎坐标钳位使用）"""
    before_click_wait: tuple[float, float] = (0.1, 0.3)   # 点击前延迟范围（模拟反应时间）
    after_click_wait: tuple[float, float] = (0.1, 0.2)    # 点击后延迟范围
    mouse_move_duration: tuple[float, float] = (0.3, 0.6) # 鼠标移动时长范围
    click_random_offset: int = 3                           # 坐标随机偏移像素
    region_jitter_ratio: float = 0.25                      # 区域中心(0.5)左右偏移比例，必须 [0, 0.5)

    def __post_init__(self):
        self.before_click_wait = _pair(self.before_click_wait)
        self.after_click_wait = _pair(self.after_click_wait)
        self.mouse_move_duration = _pair(self.mouse_move_duration)
        self.click_random_offset = int(self.click_random_offset)
        self.region_jitter_ratio = float(self.region_jitter_ratio)
        if not (0 <= self.region_jitter_ratio < 0.5):
            raise ValueError(
                f"region_jitter_ratio 必须在 [0, 0.5) 内: {self.region_jitter_ratio}")


def parse_delay_params(raw: dict | None) -> dict[str, DelayParam]:
    """dict → {key: DelayParam}（YAML/JSON 载入与测试直接传 dict 统一处理）"""
    if not raw:
        return {}
    return {
        k: v if isinstance(v, DelayParam) else DelayParam(**v)
        for k, v in raw.items()
    }


@dataclass
class MaterialGridConfig:
    """材料网格切割默认参数"""
    rows: int = 3      # 默认行数（≥ 1）
    cols: int = 6      # 默认列数（≥ 1）
    gap: int = 0       # 默认间隔(px)（≥ 0）
    height: int = 122  # 默认单cell高度(px)（≥ 1）
    width: int = 122   # 默认单cell宽度(px)（≥ 1）

    def __post_init__(self):
        for name, minimum in (("rows", 1), ("cols", 1), ("gap", 0),
                              ("height", 1), ("width", 1)):
            value = int(getattr(self, name))
            if value < minimum:
                raise ValueError(f"material_grid.{name} 必须 ≥ {minimum}: {value}")
            setattr(self, name, value)


@dataclass
class UserConfig:
    """用户配置（代码默认值 + session.json / app.yaml 覆盖，只读）

    settings / material_grid 来自 config/session/session.json（纯运行态）；
    输入模拟 input_sim + 延迟参数 delay_params 来自 config/**/app.yaml
    （system 出厂默认 ← local 用户覆盖，随版本分发，见 core.config）。
    """
    adb_capture_streaming: bool = True     # ADB 模式是否启用 scrcpy 视频流截图（false 则用 screencap）
    desktop_window_title: str = ""         # 桌面模式投屏窗口标题关键字
    desktop_background_input: bool = True  # 桌面模式是否启用后台输入（PostMessage）
    material_grid: MaterialGridConfig = field(default_factory=MaterialGridConfig)
    input_sim: InputSimConfig = field(default_factory=InputSimConfig)     # 输入模拟
    delay_params: dict[str, DelayParam] = field(default_factory=dict)     # 命名延迟参数

    def __post_init__(self):
        if isinstance(self.material_grid, dict):
            self.material_grid = MaterialGridConfig(**self.material_grid)
        if isinstance(self.input_sim, dict):
            self.input_sim = InputSimConfig(**self.input_sim)
        self.delay_params = parse_delay_params(self.delay_params)



def _session_store(session_path: Path | None = None):
    """session.json 读写入口：缺省用全局单例 SessionStore，测试传路径时构造独立实例"""
    from .core.config.session import SessionStore, get_session_store
    if session_path is None:
        return get_session_store()
    return SessionStore(session_path)


def load_user_config(session_path: Path | None = None) -> UserConfig:
    """加载用户配置：session.json（settings/material_grid）+ app.yaml（输入模拟/延迟参数）"""
    store = _session_store(session_path)

    data: dict[str, Any] = {}
    settings = store.get_node("settings")
    if isinstance(settings, dict):
        data.update(settings)
    grid = store.get_node("material_grid")
    if isinstance(grid, dict):
        data["material_grid"] = grid
    # 输入模拟 + 延迟参数：app.yaml 合并视图（system ← local）
    app = _load_app_config()
    sim = app.get("input_simulation")
    if isinstance(sim, dict):
        data["input_sim"] = sim
    params = app.get("delay_params")
    if isinstance(params, dict):
        data["delay_params"] = params
    # 忽略未知字段（settings 节点可能含旧版本/其他模块写入的 key）
    known = {f.name for f in fields(UserConfig)}
    return UserConfig(**{k: v for k, v in data.items() if k in known})


# ── 配置保存 ──

def _update_session_node(key: str, value: dict[str, Any],
                         session_path: Path | None = None) -> None:
    """整节点替换 session.json 的指定顶层节点（保留其他节点）"""
    _session_store(session_path).set_node(key, value)


def save_settings(settings: dict[str, Any], session_path: Path | None = None) -> None:
    """保存基础配置到 session.json 的 settings 节点"""
    _update_session_node("settings", settings, session_path)


def save_material_grid(grid: dict[str, Any], session_path: Path | None = None) -> None:
    """保存材料网格参数到 session.json 的 material_grid 节点"""
    _update_session_node("material_grid", grid, session_path)


# ── app.yaml（输入模拟 + 延迟参数，system/local 双层）──

APP_CONFIG_REL = "app.yaml"


def _load_app_config() -> dict[str, Any]:
    """读取 app.yaml 的 system←local 合并视图（解析失败返回空 dict）"""
    try:
        from .core.config.resolver import get_resolver
        return get_resolver().load_merged(APP_CONFIG_REL)
    except Exception as e:  # noqa: BLE001 配置缺失/损坏不应阻断启动
        logger.error(f"加载 app.yaml 失败: {e}")
        return {}


def save_app_config(input_sim: dict[str, Any], delay_params: dict[str, Any]) -> None:
    """保存输入模拟 + 延迟参数到 app.yaml（开发模式写 system 全量，用户模式写 local diff）"""
    from .core.config.resolver import get_resolver
    get_resolver().save_merged(APP_CONFIG_REL, {
        "input_simulation": input_sim,
        "delay_params": delay_params,
    })
