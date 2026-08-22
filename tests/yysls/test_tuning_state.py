"""自动调律运行状态与报告职责分离的回归测试。"""

from lvjiang.apps.yysls.workflows.implementations.tuning.state import (
    EquipmentProcessingResult,
    EquipmentSession,
    SlotEffect,
    TuningMode,
    TuningRunState,
)


def test_enter_slot_updates_slot_and_clears_locked_fingerprints():
    state = TuningRunState(current_slot="main_weapon")
    state.record_locked("locked-a")

    state.enter_slot("sub_weapon")

    assert state.current_slot == "sub_weapon"
    assert not state.is_locked("locked-a")


def test_empty_fingerprint_is_not_recorded():
    state = TuningRunState()

    state.record_locked("")

    assert state.locked_fingerprints == set()


def test_equipment_session_mode_queries_are_mutually_exclusive():
    session = EquipmentSession(expected_rating="excellent")

    assert session.mode is TuningMode.NORMAL
    assert not session.force_tune
    assert not session.tune_full_recycle

    session.mode = TuningMode.FORCE_TUNE
    assert session.force_tune
    assert not session.tune_full_recycle

    session.mode = TuningMode.TUNE_FULL_RECYCLE
    assert not session.force_tune
    assert session.tune_full_recycle


def test_processing_result_exposes_slot_effect_without_recorder_state():
    unchanged = EquipmentProcessingResult("fp-a")
    removed = EquipmentProcessingResult("fp-b", SlotEffect.REMOVED)

    assert not unchanged.slot_changed
    assert removed.slot_changed
