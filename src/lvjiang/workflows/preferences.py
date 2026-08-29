"""日常脚本的用户偏好 —— 顺序、启停、自定义显示名

存 ``config/session/session.json`` 的 ``daily.scripts`` 节点。这些是**用户
偏好**，不是配置：系统配置由开发者提供，用户想改顺序、不想看某个脚本、
想换个称呼，都不该写回系统文件（那会把系统后续新增的脚本冻住）。

节点形状::

    "daily": {
      "workflow_id": "scan_wallet",          # 上次选中的脚本（既有）
      "scripts": {
        "order":   ["scan_wallet", ...],     # 用户调整过的顺序
        "visible": {"weekly_baiye_freight": true, "scan_wallet": false},
        "names":   {"scan_wallet": "查钱包"},  # 自定义显示名
        "scopes":  {"scan_wallet": "dedicated"}  # 脚本性质覆盖
      }
    }

``visible`` 是**覆盖**而非全集：只记用户明确改过的脚本，缺省沿用作者声明
（``#% hidden: true`` 的默认不展示，其余默认展示）。因此系统新增的脚本
自动出现，不需要用户做任何事。
"""

from __future__ import annotations

from typing import NamedTuple

from loguru import logger

_NODE = "daily"
_KEY = "scripts"


def _load() -> dict:
    from ..core.config import get_session_store
    try:
        daily = get_session_store().get_node(_NODE, {})
    except Exception as e:  # noqa: BLE001 session 损坏不应让日常页打不开
        logger.warning(f"读取日常脚本偏好失败: {e}")
        return {}
    if not isinstance(daily, dict):
        return {}
    prefs = daily.get(_KEY)
    return prefs if isinstance(prefs, dict) else {}


class DailyScriptPrefs(NamedTuple):
    """日常脚本的用户偏好快照"""
    order: list[str]
    visible: dict[str, bool]
    names: dict[str, str]
    scopes: dict[str, str]


def load_preferences() -> DailyScriptPrefs:
    """读取偏好，缺失部分为空"""
    prefs = _load()
    def _list(key) -> list[str]:
        v = prefs.get(key)
        return [str(x) for x in v] if isinstance(v, list) else []
    def _map(key, cast) -> dict:
        v = prefs.get(key)
        return {str(k): cast(x) for k, x in v.items()} if isinstance(v, dict) else {}
    return DailyScriptPrefs(
        order=_list("order"),
        visible=_map("visible", bool),
        names={k: v for k, v in _map("names", str).items() if v},
        scopes={k: v for k, v in _map("scopes", str).items() if v},
    )


def save_preferences(order: list[str], visible: dict[str, bool],
                     names: dict[str, str],
                     scopes: dict[str, str] | None = None) -> None:
    """写回偏好；空值不落盘，保持 session 干净"""
    from ..core.config import get_session_store
    prefs: dict = {}
    if order:
        prefs["order"] = list(order)
    if visible:
        prefs["visible"] = {k: bool(v) for k, v in visible.items()}
    if names:
        prefs["names"] = {k: v for k, v in names.items() if v}
    if scopes:
        prefs["scopes"] = {k: v for k, v in scopes.items() if v}
    try:
        get_session_store().update_node(_NODE, {_KEY: prefs})
    except Exception as e:  # noqa: BLE001
        logger.error(f"保存日常脚本偏好失败: {e}")


# ─── 一次性迁移：local/workflows.yaml → session ────────────
#
# 0.5.4 之前顺序/勾选/显示名存在 config/local/workflows.yaml。那是错的：
# 用户偏好写进配置层会把系统后续新增的脚本冻住。这里做一次搬运，
# 搬完把旧文件改名归档。**下个版本可以删掉本段。**

_LEGACY_REL = "workflows.yaml"


def migrate_legacy_workflows_yaml() -> bool:
    """把旧的 local/workflows.yaml 搬进 session；已搬过或无旧文件返回 False"""
    import yaml

    from ..core.config.resolver import get_resolver
    legacy = get_resolver().local_dir / _LEGACY_REL
    if not legacy.exists():
        return False
    if _load():
        # session 里已有偏好，说明搬过了；旧文件留着不动，避免覆盖新偏好
        return False
    try:
        doc = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"旧 workflows.yaml 解析失败，跳过迁移: {e}")
        return False

    exposed = doc.get("exposed")
    exposed = [str(x) for x in exposed] if isinstance(exposed, list) else []
    raw_ov = doc.get("overrides")
    overrides: dict = raw_ov if isinstance(raw_ov, dict) else {}
    names = {k: v["name"] for k, v in overrides.items()
             if isinstance(v, dict) and v.get("name")}
    scopes = {k: v["scope"] for k, v in overrides.items()
              if isinstance(v, dict) and v.get("scope")}
    # 旧 exposed 是「要展示的全集」，逐项记成显式可见性覆盖；未列出的脚本
    # 当时就是不展示，但无法区分「用户取消了」和「那时还不存在」，
    # 因此只搬正向勾选，不搬负向隐藏——宁可多显示也不静默藏掉新脚本。
    visible = {sid: True for sid in exposed}
    save_preferences(exposed, visible, names, scopes)
    try:
        legacy.rename(legacy.with_suffix(".yaml.migrated"))
    except OSError as e:
        logger.warning(f"旧 workflows.yaml 归档失败（不影响迁移结果）: {e}")
    logger.info(f"日常脚本偏好已从 local/workflows.yaml 迁入 session：{len(exposed)} 项")
    return True
