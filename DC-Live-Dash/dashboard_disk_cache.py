"""Disk cache for dashboard API responses (Google Ads, Meta, GHL bulk fetches)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / ".cache" / "dashboard"


def range_cache_is_fresh(until: str, *, today: date | None = None) -> bool:
    """Past ranges stay cached; ranges through yesterday always refresh."""
    today = today or date.today()
    until_d = date.fromisoformat(until[:10])
    return (today - until_d).days > 1


def _namespace_dir(namespace: str) -> Path:
    path = _CACHE_DIR / namespace
    path.mkdir(parents=True, exist_ok=True)
    return path


def _range_filename(since: str, until: str, suffix: str) -> str:
    return f"{since}_{until}.{suffix}"


def read_parquet_range_cache(
    namespace: str,
    since: str,
    until: str,
    fetch: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    """Load a daily-metrics DataFrame from cache or fetch and store it."""
    path = _namespace_dir(namespace) / _range_filename(since, until, "parquet")
    if path.is_file() and range_cache_is_fresh(until):
        try:
            df = pd.read_parquet(path)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            meta_path = path.with_suffix(".meta.json")
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                for key, value in meta.items():
                    df.attrs[key] = value
            return df
        except Exception:
            pass

    df = fetch()
    df.to_parquet(path, index=False)
    if df.attrs:
        meta_path = path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(dict(df.attrs)), encoding="utf-8")
    return df


def read_json_range_cache(
    namespace: str,
    since: str,
    until: str,
    fetch: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Load a JSON-serializable API payload from cache or fetch and store it."""
    path = _namespace_dir(namespace) / _range_filename(since, until, "json")
    if path.is_file() and range_cache_is_fresh(until):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    payload = fetch()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def clear_dashboard_disk_cache() -> None:
    """Remove cached Google Ads, Meta, GHL bulk fetch, and tracker sheet files."""
    if not _CACHE_DIR.is_dir():
        pass
    else:
        for path in _CACHE_DIR.rglob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    try:
        from total_new_members_yoy_chart import clear_tracker_sheet_cache

        clear_tracker_sheet_cache()
    except Exception:
        pass
