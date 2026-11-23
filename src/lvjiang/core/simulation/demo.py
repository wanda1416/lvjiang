"""Explicitly synthetic, real-OCR state-machine demonstration, not a game fixture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def make_demo(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    frames = {}
    for state, text in (("start", "12345"), ("loading", "12345"), ("done", "67890")):
        image = np.full((160, 640, 3), 255, np.uint8)
        cv2.putText(image, text, (30, 110), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
        data = cv2.imencode(".png", image)[1].tobytes()
        (directory / f"{state}.png").write_bytes(data)
        frames[state] = {
            "frame": f"{state}.png",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    workflow = directory / "task.wf"
    workflow.write_text(
        """# Synthetic fixture; real OCR, no precomputed recognition answers.
find as $start by contains "12345"
if not $start
    return -1
end
click $start
find as $loading by contains "12345"
if not $loading
    return -1
end
wait 1
find as $done by contains "67890"
if $done
    return 0
end
return -1
""",
        encoding="utf-8",
    )
    spec = {
        "format_version": 1,
        "provenance": "synthetic",
        "workflow": "task.wf",
        "workflow_sha256": hashlib.sha256(workflow.read_bytes()).hexdigest(),
        "states": frames,
        "start": "start",
        "terminal": ["done"],
        "layout": {},
        "expected_return": 0,
        "limits": {"events": 200, "virtual_seconds": 10},
        "edges": [
            {
                "source": "start",
                "target": "loading",
                "action": "click",
                "match": {"box": [70, 60, 200, 130], "button": "left"},
            },
            {"source": "loading", "target": "done", "action": "timer", "after": 1},
        ],
    }
    (directory / "scenario.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
