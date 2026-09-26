"""毕业率基准 DPS 会话覆盖层。

用户可在 UI 中校正方案的 100% 毕业率基准 DPS，校正值存入 session 而非
覆写 Excel 导出的 JSON 源数据。存储在
``config/session/yysls/graduations.json``：

    {
        流派名: {
            方案名: {
                "110": {
                    "1": {"baseline_dps": float}
                }
            }
        }
    }

读取语义：session 覆盖 → JSON 默认（由调用方 fallback）。
"""
from __future__ import annotations

from loguru import logger

from . import session_node


def _load() -> dict:
    """读取毕业率覆盖文档。"""
    return session_node.load("graduations")


def get_baseline_dps(
    school_name: str, scheme_name: str, model_level: int, model_version: int,
) -> float | None:
    """读取 session 中用户校正的基准 DPS；未设置时返回 None。"""
    data = _load()
    return (
        data.get(school_name, {})
        .get(scheme_name, {})
        .get(str(model_level), {})
        .get(str(model_version), {})
        .get("baseline_dps")
    )


def set_baseline_dps(
    school_name: str, scheme_name: str, model_level: int, model_version: int,
    value: float,
) -> None:
    """写入 session 中的基准 DPS 覆盖值。"""
    value = float(value)
    if value <= 0:
        raise ValueError("100%毕业率基准 DPS 必须大于 0")
    def _apply(data: dict) -> dict:
        school = data.setdefault(school_name, {})
        scheme = school.setdefault(scheme_name, {})
        level = scheme.setdefault(str(model_level), {})
        level.setdefault(str(model_version), {})["baseline_dps"] = value
        return data

    session_node.mutate("graduations", _apply)
    logger.debug(
        "已保存毕业率基准 DPS 覆盖: "
        f"{school_name}/{scheme_name}/{model_level}/v{model_version} = {value}")


def clear_baseline_dps(
    school_name: str, scheme_name: str, model_level: int, model_version: int,
) -> None:
    """清除 session 中的基准 DPS 覆盖，回退到 JSON 默认值。"""
    def _apply(data: dict) -> dict:
        school = data.get(school_name, {})
        scheme = school.get(scheme_name) if isinstance(school, dict) else None
        level = scheme.get(str(model_level), {}) if isinstance(scheme, dict) else {}
        version = level.get(str(model_version)) if isinstance(level, dict) else None
        if isinstance(version, dict) and "baseline_dps" in version:
            del version["baseline_dps"]
            if not version:
                del level[str(model_version)]
            if not level and isinstance(scheme, dict):
                del scheme[str(model_level)]
            if not scheme and isinstance(school, dict):
                del school[scheme_name]
        return data

    session_node.mutate("graduations", _apply)
