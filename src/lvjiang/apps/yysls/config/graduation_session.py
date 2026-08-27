"""毕业率基准 DPS 会话覆盖层。

用户可在 UI 中校正方案的 100% 毕业率基准 DPS，校正值存入 session 而非
覆写 Excel 导出的 JSON 源数据。存储在 session.json 的 ``yysls`` 节点
（旧的独立 yysls.json 仍可读，见 session_node）：

    graduations: {
        流派名: {
            方案名: {
                "baseline_dps": float
            }
        }
    }

读取语义：session 覆盖 → JSON 默认（由调用方 fallback）。
"""
from __future__ import annotations

from loguru import logger

from . import session_node


def _load() -> dict:
    """读取插件会话节点（首次运行时回退旧的独立文件）"""
    return session_node.load()


def get_baseline_dps(school_name: str, scheme_name: str) -> float | None:
    """读取 session 中用户校正的基准 DPS；未设置时返回 None。"""
    data = _load()
    return (
        data.get("graduations", {})
        .get(school_name, {})
        .get(scheme_name, {})
        .get("baseline_dps")
    )


def set_baseline_dps(school_name: str, scheme_name: str, value: float) -> None:
    """写入 session 中的基准 DPS 覆盖值。"""
    value = float(value)
    if value <= 0:
        raise ValueError("100%毕业率基准 DPS 必须大于 0")
    def _apply(data: dict) -> dict:
        graduations = data.setdefault("graduations", {})
        school = graduations.setdefault(school_name, {})
        school.setdefault(scheme_name, {})["baseline_dps"] = value
        return data

    session_node.mutate(_apply)
    logger.debug(f"已保存毕业率基准 DPS 覆盖: {school_name}/{scheme_name} = {value}")


def clear_baseline_dps(school_name: str, scheme_name: str) -> None:
    """清除 session 中的基准 DPS 覆盖，回退到 JSON 默认值。"""
    def _apply(data: dict) -> dict:
        scheme = data.get("graduations", {}).get(school_name, {}).get(scheme_name)
        if scheme and "baseline_dps" in scheme:
            del scheme["baseline_dps"]
            if not scheme:
                del data["graduations"][school_name][scheme_name]
        return data

    session_node.mutate(_apply)
