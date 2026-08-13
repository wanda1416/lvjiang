from __future__ import annotations

from datetime import datetime, timedelta

from lvjiang.apps.yysls.config.profile_models import MODEL_REGEN, RegenKeyDef
from lvjiang.apps.yysls.ui.profile.cell_formatting import format_cell_tooltip
from lvjiang.apps.yysls.ui.profile.overview import (
    _compute_continuous_regen_value,
    _normalize_continuous_regen_write,
)


def test_continuous_regen_fraction_is_stored_as_time_progress():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_period="minute",
        regen_value=0.125,
    )

    before = datetime.now()
    stored_value, updated_at = _normalize_continuous_regen_write(kd, 120.111)
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
        regen_period="minute",
        regen_value=0.125,
    )
    entry = {
        "value": 100,
        "updated_at": (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds"),
    }

    current = _compute_continuous_regen_value(entry, kd)

    assert 100.49 <= current <= 100.51


def test_continuous_regen_tooltip_keeps_base_metadata():
    kd = RegenKeyDef(
        key="xinli",
        label="心力",
        regen_period="minute",
        regen_value=0.125,
        cap=600,
    )
    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    data = {
        MODEL_REGEN: {
            "xinli": {
                "value": 100,
                "updated_at": updated_at,
            }
        }
    }

    tooltip = format_cell_tooltip(kd, MODEL_REGEN, data)

    assert "更新时间:" in tooltip
    assert "回复周期: 每分钟" in tooltip
    assert "每次回复: 0.125" in tooltip
    assert "下一点恢复:" in tooltip
    assert "存储值: 100" in tooltip
    assert "上限: 600" in tooltip
    assert "精确值:" not in tooltip
    assert "展示值:" not in tooltip
