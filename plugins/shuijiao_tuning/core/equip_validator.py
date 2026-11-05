from lvjiang.apps.yysls.core.equip_validator import *  # noqa: F403


def duplicate_tuned_names(names: list[str]) -> list[str]:
    """只传入后四条名称；固定首条允许与其中一条同名。"""
    return [name for name in dict.fromkeys(names) if names.count(name) > 1]
