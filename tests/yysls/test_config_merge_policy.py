"""燕云配置合并策略声明测试。

core.config.resolver 不认识任何插件领域词汇：`yysls/tune_config.yaml` 的
`base_rules` 是登记表、`yysls/game_config.yaml` 的三张列表受保护，这些
声明不写在 resolver 的常量表里，而是由插件自己经
`apps/yysls/config/merge_policy.py` 调用 register_registry_list_paths /
register_protected_list_paths 注册（见 docs/30-architecture/05-config-layering.md）。

这里验证的是「注册确实发生」，不是合并语义本身——合并语义已由
tests/core/test_config_resolver.py 用域中立的示例路径覆盖。
"""
import lvjiang.apps.yysls.config.merge_policy  # noqa: F401  import 即触发注册
from lvjiang.apps.yysls import hooks as yysls_hooks
from lvjiang.core.config import resolver


def test_hooks_declares_merge_policy_module():
    """AppHooks.config_policy_modules 挂了 merge_policy——插件加载时才会
    真的触发上面两条注册，不是只在测试里手动 import 才生效。"""
    assert "lvjiang.apps.yysls.config.merge_policy" in yysls_hooks.config_policy_modules


def test_tune_config_base_rules_registered_as_registry_list():
    assert resolver.REGISTRY_LIST_PATHS.get("yysls/tune_config.yaml") == ("base_rules",)


def test_game_config_lists_registered_as_protected():
    assert resolver.PROTECTED_LIST_PATHS.get("yysls/game_config.yaml") == {
        "weapon_types": "name",
        "level_configs": "level",
        "season_configs": "season_number",
    }


def test_core_registry_list_paths_stays_domain_agnostic():
    """core 自己只声明 scenes.yaml——不认识任何插件领域词汇。"""
    assert "scenes.yaml" in resolver.REGISTRY_LIST_PATHS
    assert resolver.REGISTRY_LIST_PATHS["scenes.yaml"] == ("layout_scenes.*",)
