"""总览数据（profile）来源模式 —— Android 的主权开关，两端必须服从。

| 模式 | profile 数据来源 | 另一端的义务 |
| --- | --- | --- |
| ``remote``（默认） | 账号云端：快照 + 历史回放（只收不发） | PC 离线包带总览数据时**必须拒收** |
| ``pc`` | PC 离线包整库重建 | 账号在线同步**必须跳过** profile |

切换语义：``pc → remote`` 必须先「清除本地总览数据」（否则 PC 建立的库
会被云端水位判定为领先，回放语义错乱）；``remote → pc`` 随时允许。

模式标记存在 ``filesDir/profile_source.json``（离线包重建范围**之外**），
避免被包重置后模式静默翻转。
"""
from __future__ import annotations

import json
from pathlib import Path

REMOTE = "remote"
PC = "pc"


def _marker_path() -> Path:
    from ...constants import PROJECT_ROOT

    return PROJECT_ROOT.parent / "profile_source.json"


def mode() -> str:
    """当前模式；无标记时默认远端同步。"""
    try:
        data = json.loads(_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return REMOTE
    return data.get("mode") if data.get("mode") in (REMOTE, PC) else REMOTE


def set_mode(value: str) -> None:
    if value not in (REMOTE, PC):
        raise ValueError(f"未知模式：{value}")
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": value}, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def clear_local_profile() -> str:
    """清除本地总览数据并复位同步水位；返回结果说明（可直接展示）。

    只在切换回远端同步前调用：清空 profile 库 + 账号同步水位，此后首次
    同步会从云端快照重新落地。
    """
    from ..profile import repository
    from . import account_api

    repository.reset_profile_db()
    account_api._reset_sync_state()
    set_mode(REMOTE)
    return "已清除本地总览数据；下次同步将从云端快照重新获取。"
