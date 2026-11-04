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


def test_session_document_register_and_read_back(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_SESSION_DOCUMENTS", {})
    registry.register_session_document("play_styles", "yysls/play_styles.json")
    registry.register_session_document("play_styles", "yysls/play_styles.json")  # 幂等
    assert registry.session_document_for("play_styles") == registry.SessionDocument(
        "play_styles", "yysls/play_styles.json")
    assert registry.session_document_for("nothing") is None


def test_session_document_rejects_unsafe_or_duplicate_paths(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_SESSION_DOCUMENTS", {})
    monkeypatch.setattr(registry, "_USER_DOCUMENT_KINDS", {})
    for bad_path in ("/abs/x.json", "../escape.json", "yysls/../x.json",
                     "yysls\\x.json", "yysls", "yysls/x.yaml"):
        with pytest.raises(ValueError):
            registry.register_session_document("ok_kind", bad_path)
    for bad_kind in ("BadKind", "notes", "profile_yaml"):
        with pytest.raises(ValueError):
            registry.register_session_document(bad_kind, "yysls/x.json")

    registry.register_session_document("first_doc", "yysls/shared.json")
    with pytest.raises(ValueError):
        registry.register_session_document("first_doc", "yysls/other.json")
    with pytest.raises(ValueError):
        registry.register_session_document("second_doc", "yysls/shared.json")
    # 同一 kind 不能既是用户级又是账户级：两条通道的落点完全不同
    registry.register_user_document_kind("both_kinds", ".both.json")
    with pytest.raises(ValueError):
        registry.register_session_document("both_kinds", "yysls/both.json")
