"""Source-checkout entry for the isolated workflow simulator (no app bootstrap)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvjiang.core.simulation.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
