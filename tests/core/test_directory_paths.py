"""配置层可独立定位，更新缓存和索引不能从 system 位置反推。"""
from pathlib import Path

from lvjiang.core.config.content import rebuild_content
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.update_store import UpdateStore
from lvjiang.core.offline_bundle import _configuration_files
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
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(paths, "SYSTEM_CONFIG_DIR", system)
    monkeypatch.setattr(paths, "LOCAL_CONFIG_DIR", local)
    monkeypatch.setattr(paths, "UPDATES_CONFIG_DIR", updates)
    monkeypatch.setattr(paths, "I18N_CONFIG_DIR", language)
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
    files = dict(_configuration_files(config, set()))
    assert files[Path("system/scenes/example.yaml")] == system / "scenes/example.yaml"
