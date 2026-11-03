"""kind 登记表：core 固有 kind 保护、幂等登记、冲突拒绝。"""

from __future__ import annotations

import pytest

from lvjiang.core.sync import registry


def test_register_and_read_back() -> None:
    registry.register_user_document_kind("loadouts", ".loadouts.json")
    assert registry.user_document_kinds()["loadouts"] == ".loadouts.json"
    registry.register_user_document_kind("loadouts", ".loadouts.json")  # 幂等


def test_reserved_and_invalid_registrations_rejected() -> None:
    with pytest.raises(ValueError):
        registry.register_user_document_kind("user_doc", ".x.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("notes", ".x.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("BadKind", ".x.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("ok_kind", ".json")       # 等于裸 user_doc
    with pytest.raises(ValueError):
        registry.register_user_document_kind("ok_kind", ".session.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("ok_kind", "no-dot")


def test_kind_and_suffix_conflicts_rejected(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_USER_DOCUMENT_KINDS", {})
    registry.register_user_document_kind("gear_sets", ".gear_sets.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("gear_sets", ".other.json")
    with pytest.raises(ValueError):
        registry.register_user_document_kind("another", ".gear_sets.json")
