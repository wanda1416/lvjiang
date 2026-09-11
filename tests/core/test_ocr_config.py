from pathlib import Path

import yaml

from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.ocr_cleaner import OCRCleaner
from lvjiang.core.ocr_config import (
    RegionBatchConfig,
    load_ocr_config,
    load_region_batch_config,
    save_region_batch_config,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_loads_recognition_and_normalization_from_aggregate_config(
    monkeypatch, tmp_path,
):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "content_version": 1,
        "recognition": {"region_batch": {
            "min_canvas_side": 640,
            "max_content_height": 900,
            "gap": 12,
        }},
        "normalization": {"replacements": {"错": "对"}},
    })
    monkeypatch.setattr(
        resolver_module,
        "_resolver",
        ConfigResolver(system, local, dev_mode=False),
    )

    config = load_ocr_config()
    assert config["normalization"]["groups"]["equip"]["replacements"] == {"错": "对"}
    assert load_region_batch_config().gap == 12


def test_legacy_local_rules_override_until_new_local_rules_are_saved(
    monkeypatch, tmp_path,
):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "content_version": 1,
        "normalization": {"replacements": {"系统": "默认"}},
    })
    _write(local / "ocr_rules.yaml", {
        "replacements": {"旧": "用户"},
        "patterns": {"[。]": ""},
    })
    monkeypatch.setattr(
        resolver_module,
        "_resolver",
        ConfigResolver(system, local, dev_mode=False),
    )

    config = load_ocr_config()
    assert config["normalization"]["groups"]["equip"] == {
        "label": "装备词条",
        "replacements": {"旧": "用户"},
        "patterns": {"[。]": ""},
    }


def test_saving_recognition_config_preserves_normalization(monkeypatch, tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "content_version": 1,
        "recognition": {"region_batch": {"gap": 16}},
        "normalization": {"replacements": {"错": "对"}},
    })
    monkeypatch.setattr(
        resolver_module,
        "_resolver",
        ConfigResolver(system, local, dev_mode=False),
    )

    save_region_batch_config(RegionBatchConfig(800, 1600, 24))

    config = load_ocr_config()
    assert config["recognition"]["region_batch"] == {
        "min_canvas_side": 800,
        "max_content_height": 1600,
        "gap": 24,
    }
    assert config["normalization"]["groups"]["equip"]["replacements"] == {"错": "对"}


def test_cleaning_group_create_rename_delete(monkeypatch, tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "normalization": {"groups": {"equip": {
            "label": "装备词条", "replacements": {}, "patterns": {},
        }}},
    })
    monkeypatch.setattr(
        resolver_module, "_resolver",
        ConfigResolver(system, local, dev_mode=True),
    )
    OCRCleaner.reset_instance()
    cleaner = OCRCleaner()

    cleaner.add_group("dialog", "界面文字")
    assert cleaner.get_group_label("dialog") == "界面文字"
    cleaner.rename_group("dialog", "页面文字")
    assert cleaner.get_group_label("dialog") == "页面文字"
    cleaner.delete_group("dialog")
    assert "dialog" not in cleaner.get_groups()
