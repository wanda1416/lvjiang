from __future__ import annotations

from copy import deepcopy

import pytest

import lvjiang.core.profile.store as profile_store


class _SessionStore:
    def __init__(self):
        self.nodes = {
            "profile": {
                "overview_groups": {
                    "默认": {"columns": ["a", "b"]},
                },
                "alert_history": {"keep": "timestamp"},
            },
        }

    def mutate_node(self, key, mutator):
        updated = mutator(deepcopy(self.nodes.get(key)))
        self.nodes[key] = updated


@pytest.fixture
def session_store(monkeypatch):
    store = _SessionStore()
    monkeypatch.setattr(profile_store, "get_session_store", lambda: store)
    return store


def test_generic_overview_groups_overwrite_entry_does_not_exist():
    assert not hasattr(profile_store, "save_groups")


def test_explicit_column_commands_preserve_other_profile_data(session_store):
    profile_store.insert_overview_column("默认", 1, "new")
    profile_store.replace_overview_column("默认", "a", "renamed")
    profile_store.remove_overview_column("默认", "b")

    profile = session_store.nodes["profile"]
    assert profile["overview_groups"] == {
        "默认": {"columns": ["renamed", "new"]},
    }
    assert profile["alert_history"] == {"keep": "timestamp"}


def test_reorder_command_cannot_add_or_remove_columns(session_store):
    with pytest.raises(ValueError, match="cannot add or remove"):
        profile_store.reorder_overview_columns("默认", [])

    assert session_store.nodes["profile"]["overview_groups"] == {
        "默认": {"columns": ["a", "b"]},
    }


def test_explicit_group_commands_are_the_only_group_writers(session_store):
    profile_store.create_overview_group("物资")
    profile_store.rename_overview_group("物资", "资源")
    profile_store.remove_overview_group("资源")

    assert session_store.nodes["profile"]["overview_groups"] == {
        "默认": {"columns": ["a", "b"]},
    }
