"""配置数据模型

定义配置的结构化类型（dataclass），供 SessionStore / ConfigResolver 读写时使用。
不用 pydantic：它的 v2 核心是 Rust 扩展（pydantic-core），安卓设备端（Chaquopy）
无法安装；本模块只需要默认值 + 少量范围校验 + 嵌套 dict 转换，__post_init__
足以覆盖，桌面端与设备端共用同一份实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


_VALID_HOTKEYS = {f"F{i}" for i in range(7, 13)}


@dataclass
class HotkeyConfig:
    """全局热键的按键位配置（配置管理「热键设置」页维护）。

    字段名是固定的"动作"语义，值是当前绑定的按键（限 F7~F12）；
    改动写入 session.json 的 settings.hotkeys 节点，保存后由
    主窗口重建 pynput 全局监听并立即生效。
    """
    start: str = "F9"    # 开始执行工作流
    pause: str = "F8"    # 暂停 / 恢复
    stop: str = "F10"    # 停止 / 结束
    record: str = "F12"  # 脚本录制；仅在录制对话框打开期间临时全局注册

    def __post_init__(self):
        defaults = {"start": "F9", "pause": "F8", "stop": "F10", "record": "F12"}
        for name, default in defaults.items():
            value = str(getattr(self, name) or "").strip().upper()
            setattr(self, name, value if value in _VALID_HOTKEYS else default)
        # session.json 可能被手工编辑；重复键会在构造监听
        # 字典时覆盖其中一个动作，因此整组回退到唯一的默认组合。
        values = [getattr(self, name) for name in defaults]
        if len(set(values)) != len(values):
            for name, default in defaults.items():
                setattr(self, name, default)


@dataclass
class NetworkConfig:
    """三条联网行为的用户开关（session.json settings.network）。

    公告/更新是给用户的服务，统计是用户给项目的贡献——分开存放，
    ``offline`` 是总闸，勾上后其余三项在 UI 上置灰但不清空其值，
    再关掉离线模式时恢复各自原有状态。
    """
    offline: bool = False
    announcement: bool = True
    update: bool = True
    telemetry: bool = False  # 默认关；首启同意弹窗同意后才置 True

    def __post_init__(self):
        for name in ("offline", "announcement", "update", "telemetry"):
            setattr(self, name, bool(getattr(self, name)))


@dataclass
class UserConfig:
    """用户配置（代码默认值 + session.json / app.yaml 覆盖，只读）

    settings / material_grid 来自 config/session/session.json（纯运行态）；
    输入模拟 input_sim + 延迟参数 delay_params 来自 config/**/app.yaml
    （system 出厂默认 ← local 用户覆盖，随版本分发，见 core.config）。
    """
    language: str = "zh_CN"                  # 界面语言（zh_CN / en_US / auto）
    theme: str = "light"                     # 界面主题（light / dark）
    android_capture_method: str = "scrcpy"  # scrcpy / screencap
    android_input_method: str = "adb"       # adb / device_gesture（Beta，需 App）
    desktop_window_title: str = ""         # 桌面模式投屏窗口标题关键字
    desktop_background_input: bool = True  # 桌面模式是否启用后台输入（PostMessage）
    material_grid: MaterialGridConfig = field(default_factory=MaterialGridConfig)
    input_sim: InputSimConfig = field(default_factory=InputSimConfig)     # 输入模拟
    delay_params: dict[str, DelayParam] = field(default_factory=dict)     # 命名延迟参数
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)           # 全局热键按键位
    network: NetworkConfig = field(default_factory=NetworkConfig)         # 联网行为开关

    def __post_init__(self):
        if self.theme not in {"light", "dark"}:
            self.theme = "light"
        if self.android_capture_method not in {"scrcpy", "screencap"}:
            self.android_capture_method = "scrcpy"
        if self.android_input_method not in {"adb", "device_gesture"}:
            self.android_input_method = "adb"
        if isinstance(self.material_grid, dict):
            self.material_grid = MaterialGridConfig(**self.material_grid)
        if isinstance(self.input_sim, dict):
            self.input_sim = InputSimConfig(**self.input_sim)
        if isinstance(self.hotkeys, dict):
            known_hotkeys = {"start", "pause", "stop", "record"}
            self.hotkeys = HotkeyConfig(
                **{k: v for k, v in self.hotkeys.items() if k in known_hotkeys})
        if isinstance(self.network, dict):
            known_network = {"offline", "announcement", "update", "telemetry"}
            self.network = NetworkConfig(
                **{k: v for k, v in self.network.items() if k in known_network})
        self.delay_params = parse_delay_params(self.delay_params)
