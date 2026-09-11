from pathlib import Path

import yaml
from PyQt6.QtWidgets import QSpinBox

from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.ocr_cleaner import OCRCleaner
from lvjiang.core.ocr_config import load_region_batch_config
from lvjiang.ui.ocr.dialog import OCRDialog


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_recognition_config_is_third_tab_and_can_be_saved(
    qtbot, monkeypatch, tmp_path,
):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "content_version": 1,
        "recognition": {"region_batch": {
            "min_canvas_side": 736,
            "max_content_height": 1200,
            "gap": 16,
        }},
        "normalization": {"replacements": {}, "patterns": {}},
    })
    monkeypatch.setattr(
        resolver_module,
        "_resolver",
        ConfigResolver(
            system, local, dev_mode=False, remote_dir=tmp_path / "remote"
        ),
    )
    monkeypatch.setattr(OCRCleaner, "_instance", None)
    monkeypatch.setattr(OCRDialog, "_load_groups", lambda _self: None)

    dialog = OCRDialog()
    qtbot.addWidget(dialog)
    assert dialog._tabs.count() == 3
    assert dialog._tabs.tabText(2) == "识别配置"
    assert dialog._cleaning_group_combo.currentText() == "装备词条"
    assert "equip" not in dialog._cleaning_group_combo.currentText()
    assert dialog._cleaning_group_combo.minimumWidth() == 240
    assert dialog._btn_add_cleaning_group.text() == "创建规则组"
    assert dialog._btn_rename_cleaning_group.text() == "重命名规则组"
    assert dialog._btn_delete_cleaning_group.text() == "删除规则组"
    assert dialog._btn_cancel_rules.text() == "撤销"

    gap = dialog.findChild(QSpinBox, "ocr_region_gap")
    assert gap is not None and gap.value() == 16
    gap.setValue(28)
    assert dialog._btn_save_config.isEnabled()
    dialog._btn_save_config.click()

    assert load_region_batch_config().gap == 28
    assert not dialog._btn_save_config.isEnabled()
