"""yysls 声明的离线同步范围：调律规则、装备方案、玩法文档随 PC→手机快照下发。

宿主白名单机制由 ``tests/core/test_offline_bundle.py`` 用假插件覆盖；这里只
验证本插件 ``offline_configuration_patterns`` / ``offline_session_files`` 的实际取值。
"""
import zipfile

from lvjiang.apps import yysls
from lvjiang.core.offline_bundle import build_offline_bundle
from tests.fake_plugin import use_registered_hooks


def test_yysls_offline_declarations_select_its_own_files(monkeypatch, tmp_path):
    use_registered_hooks(monkeypatch, yysls.hooks)
    source = tmp_path / "pc"
    (source / "config/system/layouts").mkdir(parents=True)
    (source / "config/system/layouts/android.yaml").write_text(
        "content_version: 1\nname: Android\nenv: android\n")
    (source / "config/session/users").mkdir(parents=True)
    (source / "config/session/users/tester.json").write_text("{}")
    included = {
        "local/yysls/tuning_rules/custom.yaml", "local/yysls/gear_sets/custom.yaml",
        "system/yysls/game_config/base.yaml", "local/yysls/attr_model/nested/model.yaml",
        "system/yysls/base_attributes/鸣金·虹.yaml",
        "local/yysls/base_attributes/鸣金·虹.yaml",
        "session/yysls/play_styles.json",
    }
    excluded = {"session/yysls/unknown.json", "local/yysls/notes/unknown.yaml"}
    for relative in included | excluded:
        path = source / "config" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    archive = tmp_path / "yysls.zip"
    build_offline_bundle(source, archive, username="tester", layout="android")
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    assert {"config/" + name for name in included} <= names
    assert not {"config/" + name for name in excluded} & names
