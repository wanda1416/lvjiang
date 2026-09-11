from lvjiang.apps.yysls.config.auto_tuning_config import (
    default_auto_tuning_config,
    load_user_auto_tuning_config,
    save_user_auto_tuning_config,
)
from lvjiang.apps.yysls.config.tune_slots import DEFAULT_SLOTS
from lvjiang.core.config.wf_configs import set_wf_config
from lvjiang.core.user_config import User, save_user_metadata


def test_new_user_uses_defaults_with_no_selected_rules(tmp_path):
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)

    config = load_user_auto_tuning_config("alice", users_dir)

    assert config == default_auto_tuning_config()
    assert config["rules"] == {}
    assert config["selected_slots"] == list(DEFAULT_SLOTS)
    assert "sub_weapon" not in config["selected_slots"]


def test_legacy_session_config_is_ignored(tmp_path):
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)
    set_wf_config("auto_tuning", {
        "selected_slots": ["ring"],
        "rules": {"huiyi_general": {"enabled": True}},
    })

    config = load_user_auto_tuning_config("alice", users_dir)

    assert config["selected_slots"] == list(DEFAULT_SLOTS)
    assert config["rules"] == {}


def test_each_user_has_independent_auto_tuning_config(tmp_path):
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)
    save_user_metadata(User("bob"), users_dir)
    save_user_auto_tuning_config("alice", {
        **default_auto_tuning_config(),
        "selected_slots": ["ring"],
        "rules": {"huiyi_general": {"enabled": True}},
    }, users_dir)

    alice = load_user_auto_tuning_config("alice", users_dir)
    bob = load_user_auto_tuning_config("bob", users_dir)

    assert alice["selected_slots"] == ["ring"]
    assert alice["rules"]["huiyi_general"]["enabled"] is True
    assert bob["selected_slots"] == list(DEFAULT_SLOTS)
    assert bob["rules"] == {}
