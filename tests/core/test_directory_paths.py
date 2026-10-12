"""配置层可独立定位，更新缓存和索引不能从 system 位置反推。"""
import zipfile

from lvjiang.core.config.content import rebuild_content
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.update_store import UpdateStore
from lvjiang.core.offline_bundle import build_offline_bundle
from lvjiang.core.paths import get_config_i18n_dir


def test_independent_layer_locations_share_logical_config_context(tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.core import paths

    config = tmp_path / "config"
    system = tmp_path / "preset"
    local = tmp_path / "overrides"
    updates = tmp_path / "archives"
    session = tmp_path / "state"
    language = tmp_path / "languages"
    monkeypatch.setattr(constants, "CONFIG_DIR", config)
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", session)
    monkeypatch.setitem(paths.CONFIG_SUBDIRECTORIES, "system", system)
    monkeypatch.setitem(paths.CONFIG_SUBDIRECTORIES, "local", local)
    monkeypatch.setitem(paths.CONFIG_SUBDIRECTORIES, "updates", updates)
    monkeypatch.setitem(paths.CONFIG_SUBDIRECTORIES, "i18n", language)
    (system / "scenes").mkdir(parents=True)
    (system / "scenes/example.yaml").write_text("content_version: 1\n", encoding="utf-8")
    resolver = ConfigResolver(dev_mode=False)
    assert (resolver.system_dir, resolver.local_dir) == (system, local)
    rebuild_content(resolver)
    assert (session / "content.json").is_file()
    assert not (system.parent / "session/content.json").exists()
    store = UpdateStore(config, "0.15.0", "digest")
    assert (store.system, store.updates) == (system, updates)
    assert get_config_i18n_dir() == language
    (system / "layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  phone:\n    name: 手机\n", encoding="utf-8")
    (session / "users").mkdir()
    (session / "users/example.json").write_text("{}", encoding="utf-8")
    (session / "session.json").write_text('{"users":["example"]}', encoding="utf-8")
    bundle = tmp_path / "snapshot.zip"
    build_offline_bundle(tmp_path, bundle, username="example", layout="phone")
    with zipfile.ZipFile(bundle) as archive:
        assert archive.read("config/system/scenes/example.yaml") == (system / "scenes/example.yaml").read_bytes()
        assert "config/session/users/example.json" in archive.namelist()


def test_default_directories_resolve_current_root_without_import_time_freeze(tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.core.paths import get_config_system_dir
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "first")
    assert get_config_system_dir() == tmp_path / "first/system"
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "second")
    assert get_config_system_dir() == tmp_path / "second/system"


def test_standalone_models_use_their_own_lock_without_client_index(tmp_path):
    from lvjiang.apps.yysls.core.attr_model.manager import AttrModelManager
    first = AttrModelManager(tmp_path / "first")._resolver
    second = AttrModelManager(tmp_path / "second")._resolver
    assert first.config_dir == tmp_path / "first"
    assert second.config_dir == tmp_path / "second"
    first.write_entity("sample.yaml", "name: 示例\n")
    second.write_entity("sample.yaml", "name: 第二个\n")
    assert (tmp_path / "first/session/config.lock").exists()
    assert (tmp_path / "second/session/config.lock").exists()
    assert not (tmp_path / "first/session/content.json").exists()
    assert not (tmp_path / "second/session/content.json").exists()
