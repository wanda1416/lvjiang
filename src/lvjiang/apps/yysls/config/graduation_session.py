"""毕业率基准 DPS 会话覆盖层。

用户可在 UI 中校正方案的 100% 毕业率基准 DPS，校正值存入 session 而非
覆写 Excel 导出的 JSON 源数据。存储在 config/session/yysls.json：

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

import json
import os
import tempfile
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR

_SESSION_PATH = SESSION_CONFIG_DIR / "yysls.json"


def _load() -> dict:
    if not _SESSION_PATH.exists():
        return {}
    try:
        return json.loads(_SESSION_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"加载毕业率会话配置失败: {e}")
        return {}


def _save(data: dict) -> None:
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(
        dir=str(_SESSION_PATH.parent),
        prefix=f".{_SESSION_PATH.stem}_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, _SESSION_PATH)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


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
    data = _load()
    graduations = data.setdefault("graduations", {})
    school = graduations.setdefault(school_name, {})
    school.setdefault(scheme_name, {})["baseline_dps"] = value
    _save(data)
    logger.debug(f"已保存毕业率基准 DPS 覆盖: {school_name}/{scheme_name} = {value}")


def clear_baseline_dps(school_name: str, scheme_name: str) -> None:
    """清除 session 中的基准 DPS 覆盖，回退到 JSON 默认值。"""
    data = _load()
    scheme = (
        data.get("graduations", {}).get(school_name, {}).get(scheme_name)
    )
    if scheme and "baseline_dps" in scheme:
        del scheme["baseline_dps"]
        if not scheme:
            del data["graduations"][school_name][scheme_name]
        _save(data)
