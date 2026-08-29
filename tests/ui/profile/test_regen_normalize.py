from __future__ import annotations

from datetime import datetime, timedelta

from lvjiang.core.profile.models import (
    MODEL_QUOTA,
    MODEL_REGEN,
    QuotaKeyDef,
    RegenKeyDef,
)
from lvjiang.core.profile.periods import register_profile_period
from lvjiang.core.profile.regen import (
    compute_realtime_value,
    normalize_realtime_write,
)
from lvjiang.ui.profile.cell_formatting import (
    format_cell_tooltip,
    format_profile_cell,
)


def test_continuous_regen_fraction_is_stored_as_time_progress():
    kd = RegenKeyDef(
        key="resource_meter",
        label="资源值",
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
        key="resource_meter",
        label="资源值",
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
        key="resource_meter",
        label="资源值",
        regen_type="realtime",
        regen_rate_value=0.125,
        regen_rate_unit="minute",
        cap=600,
    )
    updated_at = (datetime.now() - timedelta(minutes=4)).isoformat(timespec="seconds")
    data = {
        MODEL_REGEN: {
            "resource_meter": {
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
        key="resource_meter",
        label="资源值",
        regen_type="realtime",
        regen_rate_value=0.13,
        regen_rate_unit="minute",
    )
    data = {
        MODEL_REGEN: {
            "resource_meter": {
                "value": 100,
                "updated_at": (
                    datetime.now() - timedelta(minutes=7, seconds=42)
                ).isoformat(timespec="seconds"),
            }
        }
    }

    text, _style = format_profile_cell(kd, MODEL_REGEN, data)

    assert text == "101"


def test_quota_tooltip_uses_registered_custom_period_label():
    register_profile_period(
        "test_tooltip_cycle",
        lambda _reset_time, now, _reset_day: now,
        label="自定义周期",
    )
    kd = QuotaKeyDef(
        key="weekly_task",
        label="周任务",
        period="test_tooltip_cycle",
        cap=10,
    )
    data = {
        MODEL_QUOTA: {
            "weekly_task": {
                "value": 3,
                "updated_at": "2026-08-29T10:00:00",
            }
        }
    }

    tooltip = format_cell_tooltip(kd, MODEL_QUOTA, data)

    assert "自定义周期上限: 10" in tooltip
