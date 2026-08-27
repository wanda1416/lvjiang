"""配置基础设施 —— 全部配置读写的唯一收口

两类数据、两个入口：
- 元数据（config/system + config/local）→ ConfigResolver
  双层合并读、按模式路由写（开发→system，用户→local 影子/diff/墓碑）
- 运行态（config/session/session.json）→ SessionStore
  全量内存缓存 + 线程锁 + 写即原子落盘，节点级 get/set

另有：
- SessionManager：users/{name}.json 的用户级 session 持久化
- load_yaml / save_yaml：通用 YAML 读写助手
- 数据模型：UserConfig / InputSimConfig / DelayParam / ReferenceGridConfig
"""
from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from .models import (
    DelayParam,
    HotkeyConfig,
    InputSimConfig,
    ReferenceGridConfig,
    UserConfig,
    parse_delay_params,
)
from .resolver import (
    DELETED_KEY,
    TOMBSTONE_SUFFIX,
    ConfigResolver,
    compute_diff,
    get_resolver,
    load_app_config,
    load_available_envs,
    merge_doc,
    save_app_config,
)
from .session import (
    SessionStore,
    get_session_store,
    load_env,
    load_reference_grid,
    load_settings,
    load_ui_page_state,
    save_env,
    save_reference_grid,
    save_settings,
    update_ui_page_state,
)
from .users import SessionManager
from .wf_configs import (
    delete_wf_config,
    get_all_wf_configs,
    get_wf_config,
    set_wf_config,
    update_wf_config,
)

__all__ = [
    # 基础设施
    "DELETED_KEY",
    "TOMBSTONE_SUFFIX",
    "ConfigResolver",
    "compute_diff",
    "get_resolver",
    "merge_doc",
    "SessionStore",
    "get_session_store",
    "load_ui_page_state",
    "update_ui_page_state",
    "SessionManager",
    "load_yaml",
    "save_yaml",
    # 数据模型
    "DelayParam",
    "HotkeyConfig",
    "InputSimConfig",
    "ReferenceGridConfig",
    "UserConfig",
    "parse_delay_params",
    # 便捷函数
    "load_app_config",
    "save_app_config",
    "load_settings",
    "save_settings",
    "load_reference_grid",
    "save_reference_grid",
    "load_env",
    "save_env",
    "load_available_envs",
    "load_user_config",
    # 工作流配置统一存储
    "get_wf_config",
    "set_wf_config",
    "update_wf_config",
    "delete_wf_config",
    "get_all_wf_configs",
]


def load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件（不存在返回空 dict）"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data else {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """保存 YAML 文件（自动建父目录，保序不排序键）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_user_config() -> UserConfig:
    """加载用户配置：session settings + app.yaml。"""
    data: dict[str, Any] = {}

    # session.json 节点
    settings = load_settings()
    if settings:
        data.update(settings)
    grid = load_reference_grid()
    if grid:
        data["reference_grid"] = grid

    # app.yaml 合并视图
    app = load_app_config()
    sim = app.get("input_simulation")
    if isinstance(sim, dict):
        data["input_sim"] = sim
    params = app.get("delay_params")
    if isinstance(params, dict):
        data["delay_params"] = params

    # 忽略未知字段（settings 节点可能含旧版本/其他模块写入的 key）
    known = {f.name for f in fields(UserConfig)}
    return UserConfig(**{k: v for k, v in data.items() if k in known})
