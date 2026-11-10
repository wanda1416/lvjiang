from pathlib import Path

import yaml

from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.ocr_cleaner import OCRCleaner
from lvjiang.core.ocr_config import (
    LIMIT_SIDE_LEN_MAX,
    PC_OCR_THREADS_MAX,
    DetectConfig,
    PcOCRConfig,
    RegionBatchConfig,
    load_detect_config,
    load_ocr_config,
    load_pc_ocr_config,
    load_region_batch_config,
    ocr_engine_options,
    save_recognition_settings,
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
        "normalization": {"default": {"replacements": {"错": "对"}}},
    })
    monkeypatch.setattr(
        resolver_module,
        "_resolver",
        ConfigResolver(system, local, dev_mode=False),
    )

    config = load_ocr_config()
    assert config["normalization"]["default"]["replacements"] == {"错": "对"}
    assert load_region_batch_config().gap == 12


def test_saving_recognition_config_preserves_normalization(monkeypatch, tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "content_version": 1,
        "recognition": {"region_batch": {"gap": 16}},
        "normalization": {"default": {"replacements": {"错": "对"}}},
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
    assert config["normalization"]["default"]["replacements"] == {"错": "对"}


def test_default_rules_apply_before_selected_group(monkeypatch, tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", {
        "normalization": {
            "default": {
                "replacements": {"甲": "乙"},
                "patterns": {"乙": "丙"},
            },
            "groups": {"equip": {
                "label": "装备词条",
                "replacements": {"丙": "丁"},
                "patterns": {"丁": "戊"},
            }},
        },
    })
    monkeypatch.setattr(
        resolver_module, "_resolver",
        ConfigResolver(system, local, dev_mode=False),
    )
    OCRCleaner.reset_instance()

    cleaner = OCRCleaner()
    assert cleaner.clean("甲") == "丙"
    assert cleaner.clean("甲", "equip") == "戊"


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


# ─── 识别性能分组（PC 并行度 / 检测输入尺寸）───────────────


def _resolver_for(monkeypatch, tmp_path, data: dict):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "ocr.yaml", data)
    monkeypatch.setattr(
        resolver_module, "_resolver", ConfigResolver(system, local, dev_mode=False)
    )


def test_recognition_performance_defaults_when_section_missing(monkeypatch, tmp_path):
    """缺分组时用出厂默认：线程 2、算子 1、检测短边 736。

    这三个默认值一起守住「不要退回 RapidOCR 的 -1/-1」——那会让三个模型各开满
    物理核互相争抢，实测既更慢又更费 CPU。
    """
    _resolver_for(monkeypatch, tmp_path, {"recognition": {}})

    pc = load_pc_ocr_config()
    assert (pc.threads, pc.inter_threads) == (2, 1)
    assert load_detect_config().limit_side_len == 736
    assert ocr_engine_options() == {
        "intra_op_num_threads": 2,
        "inter_op_num_threads": 1,
        "det_limit_side_len": 736,
    }


def test_recognition_performance_values_are_read_from_config(monkeypatch, tmp_path):
    _resolver_for(monkeypatch, tmp_path, {"recognition": {
        "pc_ocr": {"threads": 4, "inter_threads": 2},
        "detect": {"limit_side_len": 1024},
    }})

    assert load_pc_ocr_config() == PcOCRConfig(threads=4, inter_threads=2)
    assert load_detect_config() == DetectConfig(limit_side_len=1024)
    assert ocr_engine_options()["det_limit_side_len"] == 1024


def test_recognition_performance_out_of_range_is_rejected(monkeypatch, tmp_path):
    """手改配置越界要立刻报错，而不是喂给 onnxruntime 换一堆玄学表现。"""
    import pytest

    _resolver_for(monkeypatch, tmp_path, {"recognition": {"pc_ocr": {"threads": 0}}})
    with pytest.raises(ValueError, match="识别线程数"):
        load_pc_ocr_config()

    _resolver_for(monkeypatch, tmp_path, {"recognition": {
        "pc_ocr": {"threads": PC_OCR_THREADS_MAX + 1}}})
    with pytest.raises(ValueError, match="识别线程数"):
        load_pc_ocr_config()

    _resolver_for(monkeypatch, tmp_path, {"recognition": {
        "detect": {"limit_side_len": LIMIT_SIDE_LEN_MAX + 1}}})
    with pytest.raises(ValueError, match="检测输入短边"):
        load_detect_config()


def test_saving_performance_groups_preserves_other_sections(monkeypatch, tmp_path):
    """保存只覆盖自己传入的分组：区域批量与清洗规则不能被顺手回滚。"""
    _resolver_for(monkeypatch, tmp_path, {
        "content_version": 1,
        "recognition": {"region_batch": {"min_canvas_side": 800, "max_content_height": 1600, "gap": 24}},
        "normalization": {"default": {"replacements": {"错": "对"}}},
    })

    save_recognition_settings(pc_ocr=PcOCRConfig(threads=4, inter_threads=1))

    config = load_ocr_config()
    assert config["recognition"]["pc_ocr"] == {"threads": 4, "inter_threads": 1}
    assert config["recognition"]["region_batch"]["min_canvas_side"] == 800
    assert "detect" not in config["recognition"]
    assert config["normalization"]["default"]["replacements"] == {"错": "对"}

    save_recognition_settings(detect=DetectConfig(limit_side_len=512))
    config = load_ocr_config()
    assert config["recognition"]["detect"] == {"limit_side_len": 512}
    assert config["recognition"]["pc_ocr"] == {"threads": 4, "inter_threads": 1}
