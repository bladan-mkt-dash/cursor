"""Add project root to sys.path and load .env (for scripts in this folder)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EOM_DIR = Path(__file__).resolve().parent


def setup() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    eom = str(EOM_DIR)
    if eom not in sys.path:
        sys.path.insert(0, eom)
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
