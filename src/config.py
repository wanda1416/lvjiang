"""配置加载与 Pydantic 校验

配置分层（后者覆盖前者）：
1. 代码默认值（Pydantic 字段默认）
2. local/session.json 的 settings / material_grid 节点（配置管理 / 图库管理写入）
3. system/workflows.yaml 的顶层 input_delay 节点（与 flows 同级，配置管理写入）
"""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .constants import SESSION_PATH, WORKFLOWS_CONFIG_PATH


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
    height: int = Field(default=122, ge=1) # 默认单cell高度(px)
    width: int = Field(default=122, ge=1)  # 默认单cell宽度(px)


class UserConfig(BaseModel):
    """用户配置（代码默认值 + session.json / workflows.yaml 覆盖，只读）"""
    adb_capture_streaming: bool = True     # ADB 模式是否启用 scrcpy 视频流截图（false 则用 screencap）
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


def load_user_config(session_path: Path | None = None,
                     workflows_path: Path | None = None) -> UserConfig:
    """加载用户配置（代码默认值 ← session.json ← workflows.yaml input_delay）"""
    session_path = session_path or SESSION_PATH
    workflows_path = workflows_path or WORKFLOWS_CONFIG_PATH

    data: dict[str, Any] = {}
    session = _read_json(session_path)
    settings = session.get("settings")
    if isinstance(settings, dict):
        data.update(settings)
    grid = session.get("material_grid")
    if isinstance(grid, dict):
        data["material_grid"] = grid
    delay = load_yaml(workflows_path).get("input_delay")
    if isinstance(delay, dict):
        data["input_delay"] = delay
    return UserConfig(**data)


# ── 配置保存 ──

def _read_json(path: Path) -> dict[str, Any]:
    """读 JSON 文件，不存在/损坏返回空 dict"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _update_session_node(key: str, value: dict[str, Any],
                         session_path: Path | None = None) -> None:
    """读-改-写 session.json 的指定顶层节点（保留其他字段）"""
    path = session_path or SESSION_PATH
    data = _read_json(path)
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_settings(settings: dict[str, Any], session_path: Path | None = None) -> None:
    """保存基础配置到 session.json 的 settings 节点"""
    _update_session_node("settings", settings, session_path)


def save_material_grid(grid: dict[str, Any], session_path: Path | None = None) -> None:
    """保存材料网格参数到 session.json 的 material_grid 节点"""
    _update_session_node("material_grid", grid, session_path)


def _format_delay_block(delay: dict[str, Any]) -> list[str]:
    """生成 input_delay 顶层块的 YAML 文本行"""
    lines = ["# 输入操作延迟参数（配置管理页写入，覆盖 DelayConfig 代码默认值）",
             "input_delay:"]
    for key, value in delay.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"  {key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"  {key}: {value}")
    return lines


def save_input_delay(delay: dict[str, Any], workflows_path: Path | None = None) -> None:
    """保存延迟参数到 workflows.yaml 顶层 input_delay 块（与 flows 同级）

    文本级替换：存在则整块替换、不存在则末尾追加，
    避免 yaml.dump 整体重写丢掉 flows 区的注释。
    """
    path = workflows_path or WORKFLOWS_CONFIG_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    block = _format_delay_block(delay)

    start = next((i for i, ln in enumerate(lines)
                  if ln.strip() == "input_delay:" and not ln[:1].isspace()), None)
    if start is None:
        new_lines = lines + ([""] if lines and lines[-1].strip() else []) + block
    else:
        # 块范围：input_delay: 行及其后所有缩进行/空行；若紧邻上方是旧注释行一并替换
        if start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"
                                    or lines[end].strip() == "input_delay:"):
            end += 1
        tail = lines[end:]
        new_lines = lines[:start] + block + ([""] if tail else []) + tail
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")
