"""Add project root to sys.path and load .env (for scripts in Op Reports)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OP_REPORTS_DIR = Path(__file__).resolve().parent


def setup() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    op = str(OP_REPORTS_DIR)
    if op not in sys.path:
        sys.path.insert(0, op)
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
