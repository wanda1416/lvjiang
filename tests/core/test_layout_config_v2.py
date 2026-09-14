"""layouts.yaml schema v2 和稳定布局身份约束。"""

from pathlib import Path

import pytest
import yaml

from lvjiang.core.layout_config import parse_layout_entries


def test_layout_v2_separates_stable_key_and_display_name():
    entries = parse_layout_entries({
        "schema_version": 2,
        "layouts": {
            "android": {
                "name": "安卓布局",
                "canvas": {"w_ratio": 1.0, "h_ratio": 1.0},
            },
            "android_cast": {
                "name": "投屏布局",
                "extends": "android",
                "canvas": {"w_ratio": 1.0, "h_ratio": 0.92},
            },
        },
    })

    assert entries["android"].name == "安卓布局"
    assert entries["android_cast"].extends == "android"


@pytest.mark.parametrize("version", [None, 1, "2"])
def test_old_or_malformed_layout_schema_is_rejected(version):
    doc = {"layouts": {"android": {"name": "安卓布局"}}}
    if version is not None:
        doc["schema_version"] = version

    with pytest.raises(ValueError, match="仅支持 schema_version: 2"):
        parse_layout_entries(doc)


def test_duplicate_display_names_are_rejected():
    with pytest.raises(ValueError, match="布局名称重复"):
        parse_layout_entries({
            "schema_version": 2,
            "layouts": {
                "android": {"name": "同名布局"},
                "desktop": {"name": "同名布局"},
            },
        })


@pytest.mark.parametrize("key", ["Desktop", "desktop-cast", " desktop"])
def test_layout_key_must_be_stable_lower_snake_case(key):
    with pytest.raises(ValueError, match="布局 key"):
        parse_layout_entries({
            "schema_version": 2,
            "layouts": {key: {"name": "测试布局"}},
        })


def test_system_layout_keys_have_matching_layout_and_template_directories():
    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load(
        (root / "config/system/layouts.yaml").read_text(encoding="utf-8"))
    entries = parse_layout_entries(doc)

    assert list(entries) == ["android", "android_cast", "desktop"]
    assert [entry.name for entry in entries.values()] == [
        "安卓布局", "投屏布局", "桌面布局",
    ]
    for key in ("android", "desktop"):
        assert (root / "config/system/layouts" / key).is_dir()
        assert (root / "config/system/templates" / key).is_dir()
