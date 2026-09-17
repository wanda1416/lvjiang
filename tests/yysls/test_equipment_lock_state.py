from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from lvjiang.apps.yysls.core.equip_parser.lock_state import (
    classify_lock_status,
)

TEMPLATES = Path("config/system/templates/common")


def test_committed_lock_templates_are_distinguished_by_color():
    locked = cv2.imread(str(TEMPLATES / "locked.png"))
    unlocked = cv2.imread(str(TEMPLATES / "unlock.png"))

    assert classify_lock_status(locked) == "locked"
    assert classify_lock_status(unlocked) == "unlock"


def test_empty_or_ambiguous_crop_is_unknown():
    assert classify_lock_status(None) is None
    assert classify_lock_status(np.zeros((30, 32, 3), dtype=np.uint8)) is None

    ambiguous = np.full((30, 32, 3), 80, dtype=np.uint8)
    ambiguous[:2, :, 2] = 120
    assert classify_lock_status(ambiguous) is None
