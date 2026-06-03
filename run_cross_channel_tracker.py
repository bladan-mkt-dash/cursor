"""Launcher from project root — runs EOM Updates/run_cross_channel_tracker.py."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    eom = Path(__file__).resolve().parent / "EOM Updates"
    root = str(eom)
    if root not in sys.path:
        sys.path.insert(0, root)
    runpy.run_path(str(eom / "run_cross_channel_tracker.py"), run_name="__main__")
