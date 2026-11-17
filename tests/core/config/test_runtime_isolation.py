"""测试组合根必须使用真实加载器和临时可写根，不能碰用户配置。"""
from pathlib import Path

from lvjiang.core.config.resolver import get_root_resolver
from lvjiang.core.plugins import register_hooks
from lvjiang.core.scene.registry import get_registry, reload_scene_registry
from tests.fake_plugin import fake_hooks


def test_reload_and_plugin_startup_use_isolated_configuration():
    resolver = get_root_resolver()
    real = Path(__file__).parents[3] / "config"
    assert resolver.local_dir.resolve() != (real / "local").resolve()
    assert resolver.system_dir.resolve() != (real / "system").resolve()
    before = get_registry().all_scene_keys()
    assert before
    probe = resolver.local_dir / ".startup-probe"
    try:
        register_hooks(fake_hooks(startup_callbacks=[
            lambda: probe.write_text("temporary", encoding="utf-8")]), registry={})
        assert probe.read_text(encoding="utf-8") == "temporary"
        reload_scene_registry()
        assert get_registry().all_scene_keys() == before
    finally:
        probe.unlink(missing_ok=True)
