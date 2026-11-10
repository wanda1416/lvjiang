"""图像识别 → 识别配置：性能分组的读取、脏标记与保存范围。

保护两件事：新分组能在界面里读到当前配置；保存只写自己那几组，
不把区域批量识别、清洗规则一起回滚。
"""
import yaml
from PyQt6.QtWidgets import QMessageBox

from lvjiang.core.config import resolver
from lvjiang.core.ocr_config import (
    load_detect_config,
    load_ocr_config,
    load_pc_ocr_config,
    load_region_batch_config,
)
from lvjiang.ui.ocr.dialog import OCRDialog


def _dialog_with_config(qtbot, tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    (system / "ocr.yaml").write_text(
        yaml.safe_dump({
            "content_version": 1,
            "recognition": {
                "region_batch": {"min_canvas_side": 800, "max_content_height": 1600, "gap": 24},
                "pc_ocr": {"threads": 4, "inter_threads": 2},
                "detect": {"limit_side_len": 1024},
            },
            "normalization": {"default": {"replacements": {"错": "对"}}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        resolver, "_resolver", resolver.ConfigResolver(system, tmp_path / "local", dev_mode=False)
    )
    dialog = OCRDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_performance_group_reflects_saved_config(qtbot, tmp_path, monkeypatch):
    dialog = _dialog_with_config(qtbot, tmp_path, monkeypatch)

    assert dialog._pc_threads.value() == 4
    assert dialog._pc_inter_threads.value() == 2
    assert dialog._detect_limit_side.value() == 1024
    assert not dialog._btn_save_config.isEnabled()      # 未改动时不能保存


def test_editing_marks_dirty_and_cancel_restores(qtbot, tmp_path, monkeypatch):
    dialog = _dialog_with_config(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    dialog._pc_threads.setValue(1)
    assert dialog._btn_save_config.isEnabled()
    dialog._on_cancel_recognition_config()
    assert dialog._pc_threads.value() == 4
    assert not dialog._btn_save_config.isEnabled()


def test_saving_writes_only_recognition_groups(qtbot, tmp_path, monkeypatch):
    dialog = _dialog_with_config(qtbot, tmp_path, monkeypatch)

    dialog._pc_threads.setValue(2)
    dialog._detect_limit_side.setValue(512)
    dialog._on_save_recognition_config()

    assert load_pc_ocr_config().threads == 2
    assert load_detect_config().limit_side_len == 512
    assert load_region_batch_config().min_canvas_side == 800      # 未被顺手覆盖
    assert load_ocr_config()["normalization"]["default"]["replacements"] == {"错": "对"}
