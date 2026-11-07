"""界面语言的"随包分发"契约。

真实事故：0.15.11 的安装包里根本没有 `config/i18n`，`available_languages()`
按目录扫描后静默退化成"只有简体中文"，用户只能在语言下拉框里看到中文。
翻译文件本身没问题，是打包清单漏了它。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from lvjiang.i18n import available_languages

PROJECT_ROOT = Path(__file__).resolve().parents[2]
I18N_DIR = PROJECT_ROOT / "config" / "i18n"


def test_package_script_ships_config_i18n(tmp_path, monkeypatch):
    """打包脚本必须把 config\\i18n 复制到 exe 旁，否则语言下拉框没有英文。"""
    script = (PROJECT_ROOT / "packaging" / "package.bat").read_text(encoding="utf-8")
    assert "xcopy" in script
    assert "config\\i18n" in script
    assert "dist\\lvjiang\\config\\i18n" in script


def test_shipped_language_files_declare_display_names():
    """两套词表都必须带 meta.display_name，下拉框依赖它显示语言名。"""
    names = {}
    for code in ("zh_CN", "en_US"):
        data = yaml.safe_load((I18N_DIR / f"{code}.yaml").read_text(encoding="utf-8"))
        names[code] = (data.get("meta") or {}).get("display_name")
    assert names == {"zh_CN": "简体中文", "en_US": "English"}


def test_available_languages_follows_directory(tmp_path, monkeypatch):
    missing = tmp_path / "i18n"
    monkeypatch.setattr("lvjiang.i18n.get_config_i18n_dir", lambda: missing)
    assert available_languages() == [{"code": "zh_CN", "name": "简体中文"}]

    missing.mkdir()
    (missing / "zh_CN.yaml").write_text(
        "meta:\n  display_name: 简体中文\n", encoding="utf-8")
    (missing / "en_US.yaml").write_text(
        "meta:\n  display_name: English\n", encoding="utf-8")
    found = {item["code"]: item["name"] for item in available_languages()}
    assert found == {"zh_CN": "简体中文", "en_US": "English"}
