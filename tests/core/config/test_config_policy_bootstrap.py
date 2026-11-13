"""显式装配自定义配置策略，底层读写不能再自行发现插件。"""
import sys

import yaml

from lvjiang import apps
from lvjiang.apps.base import AppHooks
from lvjiang.core.config import versioning
from lvjiang.core.config.resolver import ConfigResolver


def test_policy_only_bootstrap_does_not_start_plugin_and_readers_do_not_reload_it(tmp_path, monkeypatch):
    monkeypatch.setattr(versioning, "VERSIONED_DIRS", dict(versioning.VERSIONED_DIRS))
    (tmp_path / "sample_policy.py").write_text(
        "from lvjiang.core.config.versioning import register_versioned_dir\n"
        "register_versioned_dir('sample/records', '*.yaml')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    started = []
    hooks = AppHooks(id="sample", config_policy_modules=["sample_policy"],
                     startup_callbacks=[lambda: started.append(True)])
    monkeypatch.setattr(apps, "load_app", lambda _: hooks)
    try:
        apps.load_config_policies(("sample",))
        apps.load_config_policies(("sample",))
        assert not started
        monkeypatch.setattr(apps, "load_app", lambda _: (_ for _ in ()).throw(
            AssertionError("配置读写不能装配插件")))
        resolver = ConfigResolver(config_dir=tmp_path / "config", dev_mode=True)
        resolver.write_entity("sample/records/item.yaml", "content_version: 1\nvalue: old\n")
        resolver.write_entity("sample/records/item.yaml", "content_version: 1\nvalue: new\n")
        path = resolver.resolve_read("sample/records/item.yaml")
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
            "content_version": 2, "value": "new"}
    finally:
        sys.modules.pop("sample_policy", None)
