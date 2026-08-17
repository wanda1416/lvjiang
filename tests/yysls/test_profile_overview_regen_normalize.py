from __future__ import annotations

from datetime import datetime, timedelta

from lvjiang.apps.yysls.config.profile_models import MODEL_REGEN, RegenKeyDef
from lvjiang.apps.yysls.core.profile_engine.regen_math import (
    compute_realtime_value,
    normalize_realtime_write,
)
from lvjiang.apps.yysls.ui.profile.cell_formatting import (
    format_cell_tooltip,
    format_profile_cell,
)


def test_continuous_regen_fraction_is_stored_as_time_progress():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_type="realtime",
        regen_rate_value=0.125,
        regen_rate_unit="minute",
    )

    before = datetime.now()
    stored_value, updated_at = normalize_realtime_write(kd, 120.111)
    after = datetime.now()

    assert stored_value == 120
    actual = datetime.fromisoformat(updated_at)
    expected_rollback = timedelta(seconds=0.111 * 60 / 0.125)
    assert before - expected_rollback - timedelta(seconds=1) <= actual
    assert actual <= after - expected_rollback + timedelta(seconds=1)


def test_continuous_regen_current_value_uses_second_level_progress():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_type="realtime",
        regen_rate_value=0.125,
        regen_rate_unit="minute",
    )
    entry = {
        "value": 100,
        "updated_at": (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds"),
    }

    current = compute_realtime_value(entry["value"], entry["updated_at"], kd)

    assert 100.49 <= current <= 100.51


def test_continuous_regen_tooltip_keeps_base_metadata():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_type="realtime",
        regen_rate_value=0.125,
        regen_rate_unit="minute",
        cap=600,
    )
    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    data = {
        MODEL_REGEN: {
            "xinli": {
                "value": 100,
                "updated_at": updated_at,
                "updated_time": datetime.now().isoformat(timespec="seconds"),
            }
        }
    }

    tooltip = format_cell_tooltip(kd, MODEL_REGEN, data)

    assert "更新时间:" in tooltip
    assert "写入时间:" in tooltip
    assert "恢复类型: 实时恢复" in tooltip
    assert "恢复速率: 0.125/分钟" in tooltip
    assert "下一点恢复:" in tooltip
    assert "存储值: 100" in tooltip
    assert "上限: 600" in tooltip
    assert "精确值:" not in tooltip
    assert "展示值:" not in tooltip


def test_continuous_regen_cell_display_uses_second_level_progress():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_type="realtime",
        regen_rate_value=0.13,
        regen_rate_unit="minute",
    )
    data = {
        MODEL_REGEN: {
            "xinli": {
                "value": 100,
                "updated_at": (
                    datetime.now() - timedelta(minutes=7, seconds=42)
                ).isoformat(timespec="seconds"),
            }
        }
    }

    text, _style = format_profile_cell(kd, MODEL_REGEN, data)

    assert text == "101"
