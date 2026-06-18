"""GoHighLevel (LeadConnector) API client for contacts."""

# -*- coding: utf-8 -*-

from __future__ import annotations

# Bump when hear-about normalization or fetch helpers change (war_room_data reloads).
GHL_CLIENT_REVISION = "2026-06-18-signup-search-resilient-v1"

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

# Match picklist labels in GHL; "How did you hear about us?" must map to Facebook / Instagram.
HEAR_ABOUT_US_FIELD_NAME = "How did you hear about us?"

MEMBERSHIP_LEVEL_FIELD_NAME = "Membership Level"
# GHL label may be "Committed?", "Committed", or a typo "Commited?"
COMMITTED_FIELD_NAME = "Committed?"
COMMITTED_FIELD_ALIASES = (
    "Committed?",
    "Commited?",
    "Committed",
)

# GHL labels vary ("Sign Up Date" vs "Sign-Up Date"); matching uses alphanumeric fingerprint.
SIGN_UP_DATE_FIELD_ALIASES = (
    "Sign Up Date",
    "Sign-Up Date",
)

# GHL label for membership cancellation date (DATE custom field).
CANCELLATION_DATE_FIELD_ALIASES = (
    "Membership Cancellation Date",
    "Cancellation Date",
    "Cancel Date",
)


def _bearer_token() -> str:
    return (
        os.getenv("GHL_ACCESS_TOKEN")
        or os.getenv("GHL_PRIVATE_INTEGRATION_TOKEN")
        or os.getenv("GHL_API_KEY")
        or ""
    ).strip()


def _location_id() -> str:
    return (os.getenv("GHL_LOCATION_ID") or "").strip()


def _hear_about_us_field_id() -> str:
    return (os.getenv("GHL_HEAR_ABOUT_US_FIELD_ID") or "").strip()


def _membership_level_field_id() -> str:
    return (os.getenv("GHL_MEMBERSHIP_LEVEL_FIELD_ID") or "").strip()


def _sign_up_date_field_id() -> str:
    return (os.getenv("GHL_SIGN_UP_DATE_FIELD_ID") or "").strip()


def _cancellation_date_field_id() -> str:
    return (os.getenv("GHL_CANCELLATION_DATE_FIELD_ID") or "").strip()


def _committed_field_id() -> str:
    return (os.getenv("GHL_COMMITTED_FIELD_ID") or "").strip()


def _membership_cancelled_field_id() -> str:
    return (os.getenv("GHL_MEMBERSHIP_CANCELLED_FIELD_ID") or "").strip()


def _request_headers() -> dict[str, str]:
    token = _bearer_token()
    if not token:
        raise ValueError(
            "Set GHL_ACCESS_TOKEN, GHL_PRIVATE_INTEGRATION_TOKEN, or GHL_API_KEY in .env"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _normalize_field_name(name: str) -> str:
    return "".join(c for c in name.casefold() if c.isalnum() or c.isspace()).strip()


def _field_name_fingerprint(name: str) -> str:
    """Collapse label variants (spaces vs hyphens) for loose field-name matching."""
    return "".join(c for c in name.casefold() if c.isalnum())


COMMITTED_FIELD_FINGERPRINTS = frozenset(
    _field_name_fingerprint(a) for a in COMMITTED_FIELD_ALIASES
)

MEMBERSHIP_CANCELLED_FIELD_ALIASES = (
    "Membership Cancelled",
    "Membership Canceled",
    "Membership Cancelled?",
)
MEMBERSHIP_CANCELLED_FIELD_FINGERPRINTS = frozenset(
    _field_name_fingerprint(a) for a in MEMBERSHIP_CANCELLED_FIELD_ALIASES
)


def _contact_sort_key(contact: dict[str, Any]) -> str:
    for key in ("dateAdded", "dateUpdated", "createdAt"):
        val = contact.get(key)
        if val:
            return str(val)
    return ""


def fetch_last_created_contacts(
    limit: int = 20, *, location_id: str | None = None
) -> list[dict[str, Any]]:
    """
    Fetch up to ``limit`` contacts created most recently, newest first.

    Uses POST ``/contacts/search`` with ``pageLimit`` and server-side sort on
    ``dateAdded`` descending (GHL’s creation timestamp; responses do not use a
    ``date_created`` key).

    If ``location_id`` is omitted or empty, uses ``GHL_LOCATION_ID`` from the environment.

    Environment:
        GHL_ACCESS_TOKEN — OAuth access token for the sub-account, or
        GHL_PRIVATE_INTEGRATION_TOKEN — private integration token, or
        GHL_API_KEY — alias some setups use for the same secret
        GHL_LOCATION_ID — sub-account / location ID
    """
    if not _bearer_token():
        raise ValueError(
            "Set GHL_ACCESS_TOKEN, GHL_PRIVATE_INTEGRATION_TOKEN, or GHL_API_KEY in .env"
        )
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    payload: dict[str, Any] = {
        "locationId": location_id,
        "pageLimit": limit,
        "sort": [{"field": "dateAdded", "direction": "desc"}],
    }
    data = _ghl_post_json("/contacts/search", payload)
    contacts = data.get("contacts")
    if contacts is None:
        inner = data.get("data")
        contacts = inner if isinstance(inner, list) else []
    if not isinstance(contacts, list):
        contacts = []
    return contacts[:limit]


def fetch_recent_contacts(
    limit: int = 20, *, location_id: str | None = None
) -> list[dict[str, Any]]:
    """Backward-compatible alias for :func:`fetch_last_created_contacts`."""
    return fetch_last_created_contacts(limit=limit, location_id=location_id)


def _ghl_error_detail(response: requests.Response) -> str:
    detail = response.text
    try:
        err = response.json()
        if isinstance(err, dict):
            detail = err.get("message") or err.get("error") or str(err)
    except ValueError:
        pass
    return detail


def _ghl_retryable(status_code: int, detail: str) -> bool:
    if status_code in (429, 500, 502, 503):
        return True
    if status_code == 400:
        lowered = detail.casefold()
        return "try again" in lowered or "failed to fetch" in lowered
    return False


def _ghl_post_json(
    path: str,
    payload: dict[str, Any],
    timeout: int = 60,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    last_detail = ""
    for attempt in range(max_retries):
        response = requests.post(
            url, json=payload, headers=_request_headers(), timeout=timeout
        )
        if response.ok:
            data = response.json()
            return data if isinstance(data, dict) else {}
        last_detail = _ghl_error_detail(response)
        if attempt < max_retries - 1 and _ghl_retryable(
            response.status_code, last_detail
        ):
            time.sleep(0.4 * (2**attempt))
            continue
        raise RuntimeError(f"GHL API error {response.status_code}: {last_detail}")
    raise RuntimeError(f"GHL API error: {last_detail}")


def _ghl_get_json(
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    last_detail = ""
    for attempt in range(max_retries):
        response = requests.get(
            url, params=params or {}, headers=_request_headers(), timeout=timeout
        )
        if response.ok:
            data = response.json()
            return data if isinstance(data, dict) else {}
        last_detail = _ghl_error_detail(response)
        if attempt < max_retries - 1 and _ghl_retryable(
            response.status_code, last_detail
        ):
            time.sleep(0.4 * (2**attempt))
            continue
        raise RuntimeError(f"GHL API error {response.status_code}: {last_detail}")
    raise RuntimeError(f"GHL API error: {last_detail}")


DC_APPOINTMENT_FORM_NAME_DEFAULT = "DC Appointment Form"

# Fingerprints for the “how did you hear about us?” question on form payloads (labels vary).
_FORM_SOURCE_QUESTION_FINGERPRINTS = frozenset(
    {
        _field_name_fingerprint("How did you hear about us?"),
        _field_name_fingerprint("How Did You Hear About Us?"),
        _field_name_fingerprint("Where did you hear about us?"),
        _field_name_fingerprint("How did you find us?"),
    }
)

# Skip these when guessing source from unlabeled / opaque form keys.
_FORM_FIELD_LABEL_SKIP_FINGERPRINTS = frozenset(
    {
        _field_name_fingerprint("email"),
        _field_name_fingerprint("phone"),
        _field_name_fingerprint("first name"),
        _field_name_fingerprint("last name"),
        _field_name_fingerprint("name"),
        _field_name_fingerprint("message"),
        _field_name_fingerprint("comments"),
    }
)


_custom_fields_cache: dict[str, list[dict[str, Any]]] = {}


def fetch_location_custom_fields(location_id: str | None = None) -> list[dict[str, Any]]:
    """
    List custom fields for the location.

    Used to resolve the field id for ``How did you hear about us?`` when
    ``GHL_HEAR_ABOUT_US_FIELD_ID`` is not set.
    """
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    cached = _custom_fields_cache.get(location_id)
    if cached is not None:
        return cached

    data = _ghl_get_json(f"/locations/{location_id}/customFields", timeout=60)
    fields = data.get("customFields")
    if fields is None and isinstance(data.get("data"), list):
        fields = data["data"]
    if not isinstance(fields, list):
        fields = []
    _custom_fields_cache[location_id] = fields
    return fields


def resolve_hear_about_us_custom_field_id(
    location_id: str | None = None,
    *,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> str:
    """
    Return the GHL custom field id (or key) used in search filters.

    Prefer ``GHL_HEAR_ABOUT_US_FIELD_ID`` in the environment; otherwise match
    ``field_name`` against GET /locations/.../customFields (contact model).
    """
    env_id = _hear_about_us_field_id()
    if env_id:
        return env_id

    want = _normalize_field_name(field_name)
    for f in fetch_location_custom_fields(location_id):
        if not isinstance(f, dict):
            continue
        nm = f.get("name") or f.get("fieldName") or ""
        if _normalize_field_name(str(nm)) == want:
            fid = f.get("id") or f.get("fieldKey") or f.get("key")
            if fid:
                return str(fid)

    raise ValueError(
        f"No contact custom field matching {field_name!r}. "
        "Set GHL_HEAR_ABOUT_US_FIELD_ID in .env to the field id from GHL."
    )


def _resolve_optional_custom_field_id(
    location_id: str | None,
    *,
    env_raw: str,
    field_name: str,
    cached_definitions: list[dict[str, Any]] | None,
) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Resolve a contact custom field id; reuse ``cached_definitions`` when provided."""
    env_trim = (env_raw or "").strip()
    if env_trim:
        return env_trim, cached_definitions

    want = _normalize_field_name(field_name)
    definitions = cached_definitions
    if definitions is None:
        try:
            definitions = fetch_location_custom_fields(location_id)
        except Exception:
            definitions = []
    for f in definitions:
        if not isinstance(f, dict):
            continue
        nm = f.get("name") or f.get("fieldName") or ""
        if _normalize_field_name(str(nm)) == want:
            fid = f.get("id") or f.get("fieldKey") or f.get("key")
            if fid:
                return str(fid), definitions
    return None, definitions


def _resolve_optional_custom_field_id_by_name_fingerprints(
    location_id: str | None,
    *,
    env_raw: str,
    fingerprints: frozenset[str],
    cached_definitions: list[dict[str, Any]] | None,
) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Like ``_resolve_optional_custom_field_id`` but match any field whose name fingerprint is in the set."""
    env_trim = (env_raw or "").strip()
    if env_trim:
        return env_trim, cached_definitions

    definitions = cached_definitions
    if definitions is None:
        try:
            definitions = fetch_location_custom_fields(location_id)
        except Exception:
            definitions = []
    for f in definitions:
        if not isinstance(f, dict):
            continue
        nm = f.get("name") or f.get("fieldName") or ""
        if _field_name_fingerprint(str(nm)) in fingerprints:
            fid = f.get("id") or f.get("fieldKey") or f.get("key")
            if fid:
                return str(fid), definitions
    return None, definitions


def resolve_membership_level_custom_field_id(
    location_id: str | None = None,
    *,
    field_name: str = MEMBERSHIP_LEVEL_FIELD_NAME,
) -> str | None:
    """
    Return the GHL custom field id for membership level, or None if unknown.

    Prefer ``GHL_MEMBERSHIP_LEVEL_FIELD_ID``; otherwise match ``field_name`` on
    location custom fields (same lookup as hear-about-us).
    """
    mid, _ = _resolve_optional_custom_field_id(
        location_id,
        env_raw=_membership_level_field_id(),
        field_name=field_name,
        cached_definitions=None,
    )
    return mid


def resolve_cancellation_date_custom_field_id(
    location_id: str | None = None,
) -> str | None:
    """
    Return the GHL custom field id for membership cancellation date, or None.

    Prefer ``GHL_CANCELLATION_DATE_FIELD_ID``; otherwise match labels such as
    **Membership Cancellation Date** on location custom fields.
    """
    fps = frozenset(_field_name_fingerprint(a) for a in CANCELLATION_DATE_FIELD_ALIASES)
    cid, _ = _resolve_optional_custom_field_id_by_name_fingerprints(
        location_id,
        env_raw=_cancellation_date_field_id(),
        fingerprints=fps,
        cached_definitions=None,
    )
    return cid


def resolve_sign_up_date_custom_field_id(
    location_id: str | None = None,
) -> str | None:
    """
    Return the GHL custom field id for sign-up date, or None if unknown.

    Prefer ``GHL_SIGN_UP_DATE_FIELD_ID``; otherwise match common labels such as
    "Sign Up Date" / "Sign-Up Date" on location custom fields.
    """
    fps = frozenset(_field_name_fingerprint(a) for a in SIGN_UP_DATE_FIELD_ALIASES)
    sid, _ = _resolve_optional_custom_field_id_by_name_fingerprints(
        location_id,
        env_raw=_sign_up_date_field_id(),
        fingerprints=fps,
        cached_definitions=None,
    )
    return sid


def resolve_committed_custom_field_id(
    location_id: str | None = None,
    *,
    field_name: str = COMMITTED_FIELD_NAME,
) -> str | None:
    """
    Return the GHL custom field id for the Committed field, or None if unknown.

    Prefer ``GHL_COMMITTED_FIELD_ID``; otherwise match ``field_name`` or any
    label in ``COMMITTED_FIELD_ALIASES`` on location custom fields.
    """
    env_id = (_committed_field_id() or "").strip()
    if env_id:
        return env_id
    cid, defs = _resolve_optional_custom_field_id(
        location_id,
        env_raw="",
        field_name=field_name,
        cached_definitions=None,
    )
    if cid:
        return cid
    cid2, _ = _resolve_optional_custom_field_id_by_name_fingerprints(
        location_id,
        env_raw="",
        fingerprints=COMMITTED_FIELD_FINGERPRINTS,
        cached_definitions=defs,
    )
    return cid2


def resolve_membership_cancelled_custom_field_id(
    location_id: str | None = None,
) -> str | None:
    """
    Return the GHL custom field id for **Membership Cancelled** (boolean / Yes-No),
    or None if unknown.

    Prefer ``GHL_MEMBERSHIP_CANCELLED_FIELD_ID``; otherwise match common labels.
    """
    env_id = (_membership_cancelled_field_id() or "").strip()
    if env_id:
        return env_id
    cid, defs = _resolve_optional_custom_field_id(
        location_id,
        env_raw="",
        field_name="Membership Cancelled",
        cached_definitions=None,
    )
    if cid:
        return cid
    cid2, _ = _resolve_optional_custom_field_id_by_name_fingerprints(
        location_id,
        env_raw="",
        fingerprints=MEMBERSHIP_CANCELLED_FIELD_FINGERPRINTS,
        cached_definitions=defs,
    )
    return cid2


def _parse_contact_date_added_ms(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v if v > 10_000_000_000 else int(v * 1000)
    s = str(raw).strip()
    if s.isdigit():
        v = int(s)
        return v if v > 10_000_000_000 else int(v * 1000)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def contact_created_utc_date_str(contact: dict[str, Any]) -> str | None:
    """Calendar date ``YYYY-MM-DD`` in UTC from ``dateAdded`` or ``createdAt``."""
    ms = _parse_contact_date_added_ms(
        contact.get("dateAdded") or contact.get("createdAt")
    )
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


META_LEAD_TAG = "meta lead"
GOOGLE_LEAD_TAG = "dc thru g-ad"


def contact_tag_names(contact: dict[str, Any]) -> list[str]:
    """Normalized tag names from a GHL contact (strings or ``{name: ...}`` objects)."""
    out: list[str] = []
    for tag in contact.get("tags") or []:
        if isinstance(tag, str):
            name = tag
        else:
            name = str((tag or {}).get("name") or "")
        name = name.strip()
        if name:
            out.append(name)
    return out


def contact_has_tag(contact: dict[str, Any], tag_name: str) -> bool:
    target = (tag_name or "").strip().casefold()
    if not target:
        return False
    return any(t.casefold() == target for t in contact_tag_names(contact))


def _contact_attribution_dicts(contact: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("attributionSource", "lastAttributionSource"):
        val = contact.get(key)
        if isinstance(val, dict):
            out.append(val)
    return out


def contact_fired_meta_pixel(contact: dict[str, Any]) -> bool:
    """True when GHL attribution captured a Meta pixel signal (``fbp`` / ``fbc``)."""
    for attr in _contact_attribution_dicts(contact):
        if attr.get("fbp") or attr.get("fbc"):
            return True
    return False


def contact_fired_google_tag(contact: dict[str, Any]) -> bool:
    """True when GHL attribution captured Google Tag / gtag (``gaClientId``)."""
    for attr in _contact_attribution_dicts(contact):
        if attr.get("gaClientId"):
            return True
    return False


def is_meta_lead_contact(contact: dict[str, Any]) -> bool:
    """Meta lead = ``meta lead`` tag or Meta pixel fired on the contact."""
    return contact_has_tag(contact, META_LEAD_TAG) or contact_fired_meta_pixel(contact)


def is_google_lead_contact(contact: dict[str, Any]) -> bool:
    """Google lead = ``dc thru g-ad`` tag or Google Tag fired on the contact."""
    return contact_has_tag(contact, GOOGLE_LEAD_TAG) or contact_fired_google_tag(contact)


def fetch_leads_by_date_added(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any]:
    """
    Classify new GHL contacts (``dateAdded`` in range) for paid-channel lead counts.

    - **Total leads** — every contact created in the window.
    - **Meta leads** — ``meta lead`` tag and/or Meta pixel (``fbp`` / ``fbc``).
    - **Google leads** — ``dc thru g-ad`` tag and/or Google Tag (``gaClientId``).

    Returns monthly roll-ups keyed by month-start timestamp (``YYYY-MM-01``) plus totals.
    """
    contacts, truncated = fetch_contacts_date_added_complete(
        since, until, location_id=location_id
    )
    total_reported = len(contacts)

    meta_by_month: dict[str, int] = {}
    google_by_month: dict[str, int] = {}
    total_by_month: dict[str, int] = {}
    meta_total = google_total = 0

    for contact in contacts:
        created = contact_created_utc_date_str(contact)
        if not created:
            continue
        month_key = created[:7] + "-01"
        total_by_month[month_key] = total_by_month.get(month_key, 0) + 1

        if is_meta_lead_contact(contact):
            meta_by_month[month_key] = meta_by_month.get(month_key, 0) + 1
            meta_total += 1
        if is_google_lead_contact(contact):
            google_by_month[month_key] = google_by_month.get(month_key, 0) + 1
            google_total += 1

    monthly: list[dict[str, Any]] = []
    for month_start, _ in _month_periods_inclusive(since, until):
        monthly.append(
            {
                "month_start": month_start,
                "total_new_contacts": int(total_by_month.get(month_start, 0)),
                "meta_leads": int(meta_by_month.get(month_start, 0)),
                "google_leads": int(google_by_month.get(month_start, 0)),
            }
        )

    return {
        "since": since,
        "until": until,
        "contacts_loaded": len(contacts),
        "total_new_contacts": len(contacts),
        "meta_leads": meta_total,
        "google_leads": google_total,
        "monthly": monthly,
        "truncated_pages": truncated,
        "total_reported": total_reported,
    }


def _calendar_dates_inclusive(since: str, until: str) -> list[str]:
    """Inclusive ``YYYY-MM-DD`` strings from ``since`` through ``until``."""
    a = datetime.strptime(since, "%Y-%m-%d").date()
    b = datetime.strptime(until, "%Y-%m-%d").date()
    out: list[str] = []
    d = a
    while d <= b:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _inclusive_utc_range_ms(since: str, until: str) -> tuple[int, int]:
    """Inclusive calendar range [since, until] in UTC, as millisecond timestamps."""
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_day = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = end_day + timedelta(days=1) - timedelta(microseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _ghl_custom_field_value_to_str(val: Any) -> str:
    """Turn GHL custom field payloads (including date / option objects) into display text."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        n = float(val)
        if n == int(n):
            iv = int(n)
            if iv > 10_000_000_000:
                try:
                    dt = datetime.fromtimestamp(iv / 1000.0, tz=timezone.utc)
                    return dt.strftime("%Y-%m-%d")
                except (OSError, OverflowError, ValueError):
                    pass
            if 1_000_000_000 < iv < 10_000_000_000:
                try:
                    dt = datetime.fromtimestamp(float(iv), tz=timezone.utc)
                    return dt.strftime("%Y-%m-%d")
                except (OSError, OverflowError, ValueError):
                    pass
        s = str(val).strip()
        return s if s not in ("{}", "[]", "null", "None") else ""
    if isinstance(val, dict):
        for k in (
            "value",
            "fieldValue",
            "date",
            "selectedValue",
            "name",
            "label",
            "text",
            "option",
        ):
            if k not in val or val[k] in (None, "", []):
                continue
            inner = _ghl_custom_field_value_to_str(val[k])
            if inner:
                return inner
        return ""
    if isinstance(val, list):
        parts = [_ghl_custom_field_value_to_str(x) for x in val]
        return ", ".join(p for p in parts if p)
    s = str(val).strip()
    if s in ("{}", "[]", "null", "None"):
        return ""
    return s


def contact_custom_field_value(contact: dict[str, Any], field_id: str) -> str:
    if not (field_id or "").strip():
        return ""
    cf = contact.get("customFields")
    if isinstance(cf, dict):
        v = cf.get(field_id)
        return _ghl_custom_field_value_to_str(v)
    if not isinstance(cf, list):
        return ""
    fid = str(field_id)
    for item in cf:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "")
        ikey = str(item.get("key") or item.get("fieldKey") or "")
        if iid == fid or ikey == fid:
            val = item.get("value")
            if val is None:
                val = item.get("fieldValue")
            return _ghl_custom_field_value_to_str(val)
    return ""


def _is_facebook_or_instagram_source(value: str) -> bool:
    v = value.strip().casefold()
    return v in ("facebook", "instagram")


def _search_contacts_page(
    location_id: str,
    filters: list[dict[str, Any]],
    *,
    page_limit: int,
    search_after: list[Any] | None,
) -> tuple[list[dict[str, Any]], list[Any] | None, int | None]:
    payload: dict[str, Any] = {
        "locationId": location_id,
        "pageLimit": page_limit,
        "filters": filters,
    }
    if search_after:
        payload["searchAfter"] = search_after

    data = _ghl_post_json("/contacts/search", payload)
    contacts = data.get("contacts")
    if contacts is None:
        inner = data.get("data")
        contacts = inner if isinstance(inner, list) else []
    if not isinstance(contacts, list):
        contacts = []

    next_cursor: list[Any] | None = None
    for key in ("searchAfter", "nextSearchAfter"):
        cur = data.get(key)
        if isinstance(cur, list) and cur:
            next_cursor = cur
            break
    meta = data.get("meta")
    if next_cursor is None and isinstance(meta, dict):
        cur = meta.get("searchAfter") or meta.get("nextSearchAfter")
        if isinstance(cur, list) and cur:
            next_cursor = cur

    total_reported: int | None = None
    tr = data.get("total")
    if isinstance(tr, int):
        total_reported = tr
    elif isinstance(tr, str) and tr.isdigit():
        total_reported = int(tr)

    return contacts, next_cursor, total_reported


def search_contacts_by_custom_field_equals(
    field_id: str,
    value: str,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = 50,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Return contacts matching customFields.<field_id> == value (paginated).

    Uses POST /contacts/search with ``filters`` (operator ``eq``).
    The second return value is True if more results may exist (pagination cap).
    """
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    filters = [
        {
            "field": f"customFields.{field_id}",
            "operator": "eq",
            "value": value,
        }
    ]

    out: list[dict[str, Any]] = []
    cursor: list[Any] | None = None
    for _ in range(max_pages):
        batch, cursor, _ = _search_contacts_page(
            location_id, filters, page_limit=page_limit, search_after=cursor
        )
        out.extend(batch)
        if not batch or len(batch) < page_limit or not cursor:
            break
    else:
        return out, True
    return out, False


def search_contacts_date_added_range(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = 500,
) -> tuple[list[dict[str, Any]], bool, int]:
    """
    Contacts whose ``dateAdded`` falls in the inclusive UTC range [since, until].

    Uses POST ``/contacts/search`` with ``dateAdded`` ``range`` (millisecond timestamps)
    and **page**-based pagination (1-based). For this filter shape the API often omits
    ``searchAfter``, so ``page`` is required to retrieve all rows.

    Returns:
        (contacts, truncated_pages, total_reported)
        ``total_reported`` is from the API (first page), or 0 if absent.
    """
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    start_ms, end_ms = _inclusive_utc_range_ms(since, until)
    filters: list[dict[str, Any]] = [
        {
            "field": "dateAdded",
            "operator": "range",
            "value": {"gte": start_ms, "lte": end_ms},
        }
    ]

    out: list[dict[str, Any]] = []
    total_reported = 0
    truncated = False
    for p in range(1, max_pages + 1):
        payload: dict[str, Any] = {
            "locationId": location_id,
            "pageLimit": page_limit,
            "page": p,
            "filters": filters,
        }
        data = _ghl_post_json("/contacts/search", payload)
        batch = data.get("contacts")
        if batch is None:
            inner = data.get("data")
            batch = inner if isinstance(inner, list) else []
        if not isinstance(batch, list):
            batch = []
        if p == 1:
            tr = data.get("total")
            if isinstance(tr, int):
                total_reported = tr
            elif isinstance(tr, str) and tr.isdigit():
                total_reported = int(tr)
        out.extend(batch)
        if not batch or len(batch) < page_limit:
            break
    else:
        truncated = True

    return out, truncated, total_reported


# GHL returns HTTP 400 around page ~101 for large ``dateAdded`` windows.
_GHL_DATE_ADDED_MAX_PAGES = 100
_GHL_DATE_ADDED_MIN_SPLIT_MS = 60_000

# ``/contacts/search``: page * pageLimit must not exceed 10_000 (400 beyond page 100).
_GHL_CONTACT_SEARCH_MAX_PAGES = 100


def _search_contacts_date_added_ms_range(
    start_ms: int,
    end_ms: int,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = _GHL_DATE_ADDED_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Page through contacts for an exact ``dateAdded`` millisecond window.

    Returns ``(contacts, truncated)`` where ``truncated`` is True when the page
    cap was hit (more rows likely exist).
    """
    contacts, truncated, _ = _search_contacts_date_added_ms_range_reported(
        start_ms,
        end_ms,
        location_id=location_id,
        page_limit=page_limit,
        max_pages=max_pages,
    )
    return contacts, truncated


def _search_contacts_date_added_ms_range_reported(
    start_ms: int,
    end_ms: int,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = _GHL_DATE_ADDED_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Like ``_search_contacts_date_added_ms_range`` but also returns API ``total``."""
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    filters: list[dict[str, Any]] = [
        {
            "field": "dateAdded",
            "operator": "range",
            "value": {"gte": start_ms, "lte": end_ms},
        }
    ]

    out: list[dict[str, Any]] = []
    total_reported = 0
    for p in range(1, max_pages + 1):
        payload: dict[str, Any] = {
            "locationId": location_id,
            "pageLimit": page_limit,
            "page": p,
            "filters": filters,
        }
        data = _ghl_post_json("/contacts/search", payload)
        batch = data.get("contacts")
        if batch is None:
            inner = data.get("data")
            batch = inner if isinstance(inner, list) else []
        if not isinstance(batch, list):
            batch = []
        if p == 1:
            tr = data.get("total")
            if isinstance(tr, int):
                total_reported = tr
            elif isinstance(tr, str) and tr.isdigit():
                total_reported = int(tr)
        out.extend(batch)
        if not batch or len(batch) < page_limit:
            return out, False, total_reported
    return out, True, total_reported


def _fetch_contacts_date_added_ms_adaptive(
    start_ms: int,
    end_ms: int,
    *,
    location_id: str | None = None,
    depth: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    """Recursively split oversized ``dateAdded`` windows that exceed GHL page limits."""
    if end_ms < start_ms:
        return [], False

    try:
        batch, truncated = _search_contacts_date_added_ms_range(
            start_ms, end_ms, location_id=location_id
        )
    except RuntimeError:
        if (
            depth > 24
            or end_ms - start_ms <= _GHL_DATE_ADDED_MIN_SPLIT_MS
        ):
            raise
        mid = (start_ms + end_ms) // 2
        left, t_left = _fetch_contacts_date_added_ms_adaptive(
            start_ms, mid, location_id=location_id, depth=depth + 1
        )
        right, t_right = _fetch_contacts_date_added_ms_adaptive(
            mid + 1, end_ms, location_id=location_id, depth=depth + 1
        )
        merged = {str(c.get("id") or ""): c for c in left + right if c.get("id")}
        return list(merged.values()), t_left or t_right

    if truncated and end_ms - start_ms > _GHL_DATE_ADDED_MIN_SPLIT_MS:
        mid = (start_ms + end_ms) // 2
        left, t_left = _fetch_contacts_date_added_ms_adaptive(
            start_ms, mid, location_id=location_id, depth=depth + 1
        )
        right, t_right = _fetch_contacts_date_added_ms_adaptive(
            mid + 1, end_ms, location_id=location_id, depth=depth + 1
        )
        merged = {str(c.get("id") or ""): c for c in left + right if c.get("id")}
        return list(merged.values()), t_left or t_right

    return batch, truncated


def _calendar_day_chunks(since: str, until: str) -> list[tuple[str, str]]:
    """Inclusive daily ``YYYY-MM-DD`` windows from ``since`` through ``until``."""
    range_start = datetime.strptime(since, "%Y-%m-%d").date()
    range_end = datetime.strptime(until, "%Y-%m-%d").date()
    chunks: list[tuple[str, str]] = []
    cursor = range_start
    while cursor <= range_end:
        chunks.append((cursor.isoformat(), cursor.isoformat()))
        cursor += timedelta(days=1)
    return chunks


def _hourly_ms_chunks(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """Split a millisecond window into one-hour slices (inclusive)."""
    chunks: list[tuple[int, int]] = []
    hour_ms = 3_600_000
    cursor = start_ms
    while cursor <= end_ms:
        chunk_end = min(cursor + hour_ms - 1, end_ms)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + 1
    return chunks


def _load_contacts_for_day_ms(
    start_ms: int,
    end_ms: int,
    *,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Load one calendar day of ``dateAdded`` contacts.

    Tries the full day first, then hourly windows, then recursive splits for
    bulk-import spikes that exceed GHL's ~100 page pagination cap.
    """
    page_limit = 100
    page_cap = _GHL_DATE_ADDED_MAX_PAGES * page_limit

    try:
        probe, truncated_probe, total_reported = (
            _search_contacts_date_added_ms_range_reported(
                start_ms, end_ms, location_id=location_id, max_pages=1
            )
        )
    except RuntimeError:
        probe, truncated_probe, total_reported = [], False, 0

    needs_split = total_reported > page_cap or (
        len(probe) >= page_limit and total_reported > page_limit
    )

    if not needs_split:
        if len(probe) < page_limit:
            return probe, False
        try:
            batch, truncated, _ = _search_contacts_date_added_ms_range_reported(
                start_ms, end_ms, location_id=location_id
            )
            if not truncated:
                return batch, False
        except RuntimeError:
            pass

    combined: dict[str, dict[str, Any]] = {}
    day_truncated = needs_split or truncated_probe
    for hour_start, hour_end in _hourly_ms_chunks(start_ms, end_ms):
        hour_batch, hour_truncated = _fetch_contacts_date_added_ms_adaptive(
            hour_start, hour_end, location_id=location_id
        )
        day_truncated = day_truncated or hour_truncated
        for contact in hour_batch:
            cid = str(contact.get("id") or "")
            if cid:
                combined[cid] = contact
    return list(combined.values()), day_truncated


def fetch_contacts_date_added_complete(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Load all contacts created in ``[since, until]`` (inclusive calendar dates).

    Uses daily windows and recursively splits any window that hits GHL's deep
    pagination limit (~100 pages), which otherwise returns HTTP 400 and yields
    zero leads in downstream dashboards.
    """
    combined: dict[str, dict[str, Any]] = {}
    truncated = False
    for chunk_since, chunk_until in _calendar_day_chunks(since, until):
        start_ms, end_ms = _inclusive_utc_range_ms(chunk_since, chunk_until)
        batch, part_truncated = _load_contacts_for_day_ms(
            start_ms, end_ms, location_id=location_id
        )
        truncated = truncated or part_truncated
        for contact in batch:
            cid = str(contact.get("id") or "")
            if cid:
                combined[cid] = contact
    return list(combined.values()), truncated


def load_contacts_for_calendar_day(
    day: str,
    *,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """All contacts whose ``dateAdded`` falls on ``day`` (``YYYY-MM-DD``, UTC)."""
    start_ms, end_ms = _inclusive_utc_range_ms(day, day)
    return _load_contacts_for_day_ms(start_ms, end_ms, location_id=location_id)


def classify_hear_about_wom_vs_google(raw: str) -> str | None:
    """
    Map **How did you hear about us?** text to **Word of mouth** or **Google**.

    - **Word of mouth**: value contains ``word of mouth`` (case-insensitive), e.g.
      picklist labels like "Word of mouth (e.g. family member, friend, etc.)".
    - **Google**: value contains ``google`` (case-insensitive), checked after the WOM rule.
    """
    v = (raw or "").strip().casefold()
    if not v:
        return None
    if "word of mouth" in v:
        return "Word of mouth"
    if "google" in v:
        return "Google"
    return None


def fetch_hear_about_wom_google_monthly_by_date_added(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Contacts created in [since, until] (UTC ``dateAdded``) whose **How did you hear about us?**
    is non-blank and classifies as **Word of mouth** (*word of mouth* substring) or **Google**
    (``google`` substring). Aggregated by calendar month of ``dateAdded``.
    """
    field_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    contacts, truncated, total_reported = search_contacts_date_added_range(
        since, until, location_id=location_id
    )
    start_ms, end_ms = _inclusive_utc_range_ms(since, until)

    by_channel: dict[str, int] = {"Word of mouth": 0, "Google": 0}
    blank_field = 0
    other_value = 0
    date_mismatch = 0
    per_month: dict[str, dict[str, int]] = {}

    for c in contacts:
        ms = _parse_contact_date_added_ms(c.get("dateAdded") or c.get("createdAt"))
        if ms is None or not (start_ms <= ms <= end_ms):
            date_mismatch += 1
            continue
        raw = contact_custom_field_value(c, field_id)
        if not (raw or "").strip():
            blank_field += 1
            continue
        channel = classify_hear_about_wom_vs_google(raw)
        if channel is None:
            other_value += 1
            continue
        by_channel[channel] = by_channel.get(channel, 0) + 1
        d = contact_created_utc_date_str(c)
        if not d:
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        ym = f"{day.year:04d}-{day.month:02d}"
        bucket = per_month.setdefault(ym, {"Word of mouth": 0, "Google": 0})
        bucket[channel] += 1

    monthly: list[dict[str, Any]] = []
    for month_start, month_label in _month_periods_inclusive(since, until):
        ym = month_start[:7]
        vals = per_month.get(ym, {"Word of mouth": 0, "Google": 0})
        monthly.append(
            {
                "month_start": month_start,
                "month_label": month_label,
                "word_of_mouth": int(vals["Word of mouth"]),
                "google": int(vals["Google"]),
            }
        )

    return {
        "since": since,
        "until": until,
        "field_id": field_id,
        "field_name": field_name,
        "monthly": monthly,
        "by_channel": by_channel,
        "truncated_pages": truncated,
        "total_reported_in_range": total_reported,
        "contacts_loaded": len(contacts),
        "blank_hear_about_in_range": blank_field,
        "other_hear_about_in_range": other_value,
        "date_mismatch_skipped": date_mismatch,
    }


def fetch_facebook_instagram_conversions(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Contacts whose ``How did you hear about us?`` is Facebook or Instagram,
    with ``dateAdded`` in the inclusive UTC range [since, until] (YYYY-MM-DD).

    Align this window with Meta campaign insights (e.g. ZM Primary Care weekly
    chart defaults).

    Environment:
        Same as ``fetch_recent_contacts``, plus optional
        ``GHL_HEAR_ABOUT_US_FIELD_ID`` to skip resolving the field by name.
        Optional ``GHL_MEMBERSHIP_LEVEL_FIELD_ID`` / ``GHL_SIGN_UP_DATE_FIELD_ID``
        for table columns.

    Returns:
        {
            "since": str,
            "until": str,
            "field_id": str,
            "field_name": str,
            "membership_level_field_id": str,  # "" if not resolved
            "sign_up_date_field_id": str,  # "" if not resolved
            "contacts": list of contact dicts (deduped),
            "by_source": {"Facebook": n, "Instagram": n},
            "daily": list of {
                "date_start": str,  # YYYY-MM-DD UTC
                "facebook": int,
                "instagram": int,
                "total": int,
            }  # one row per calendar day in [since, until], zeros filled
            "truncated_pages": bool  # True if pagination stopped early (safety cap)
        }
    """
    field_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    defs: list[dict[str, Any]] | None = None
    mid, defs = _resolve_optional_custom_field_id(
        location_id,
        env_raw=_membership_level_field_id(),
        field_name=MEMBERSHIP_LEVEL_FIELD_NAME,
        cached_definitions=defs,
    )
    sign_fps = frozenset(_field_name_fingerprint(a) for a in SIGN_UP_DATE_FIELD_ALIASES)
    sid, defs = _resolve_optional_custom_field_id_by_name_fingerprints(
        location_id,
        env_raw=_sign_up_date_field_id(),
        fingerprints=sign_fps,
        cached_definitions=defs,
    )
    membership_level_field_id = mid or ""
    sign_up_date_field_id = sid or ""
    start_ms, end_ms = _inclusive_utc_range_ms(since, until)

    truncated = False
    combined: dict[str, dict[str, Any]] = {}
    for label in ("Facebook", "Instagram"):
        raw, more = search_contacts_by_custom_field_equals(
            field_id, label, location_id=location_id
        )
        truncated = truncated or more
        for c in raw:
            cid = str(c.get("id") or "")
            if cid:
                combined[cid] = c

    filtered: list[dict[str, Any]] = []
    by_source: dict[str, int] = {"Facebook": 0, "Instagram": 0}
    for c in combined.values():
        added_ms = _parse_contact_date_added_ms(
            c.get("dateAdded") or c.get("createdAt")
        )
        if added_ms is None or not (start_ms <= added_ms <= end_ms):
            continue
        src = contact_custom_field_value(c, field_id)
        if not _is_facebook_or_instagram_source(src):
            continue
        filtered.append(c)
        key = "Facebook" if src.strip().casefold() == "facebook" else "Instagram"
        by_source[key] = by_source.get(key, 0) + 1

    filtered.sort(key=_contact_sort_key, reverse=True)

    per_day: dict[str, dict[str, int]] = {}
    for c in filtered:
        d = contact_created_utc_date_str(c)
        if not d:
            continue
        bucket = per_day.setdefault(d, {"Facebook": 0, "Instagram": 0})
        src_raw = contact_custom_field_value(c, field_id).strip().casefold()
        if src_raw == "facebook":
            bucket["Facebook"] += 1
        elif src_raw == "instagram":
            bucket["Instagram"] += 1

    daily: list[dict[str, Any]] = []
    for ds in _calendar_dates_inclusive(since, until):
        vals = per_day.get(ds, {"Facebook": 0, "Instagram": 0})
        fb = vals["Facebook"]
        ig = vals["Instagram"]
        daily.append(
            {
                "date_start": ds,
                "facebook": fb,
                "instagram": ig,
                "total": fb + ig,
            }
        )

    return {
        "since": since,
        "until": until,
        "field_id": field_id,
        "field_name": field_name,
        "membership_level_field_id": membership_level_field_id,
        "sign_up_date_field_id": sign_up_date_field_id,
        "contacts": filtered,
        "by_source": by_source,
        "daily": daily,
        "truncated_pages": truncated,
    }


def _month_periods_inclusive(since: str, until: str) -> list[tuple[str, str]]:
    """
    Calendar months from the month containing ``since`` through the month
    containing ``until`` (inclusive).

    Returns list of (month_start_iso, display_label) e.g. ("2025-09-01", "Sep 2025").
    """
    a = datetime.strptime(since, "%Y-%m-%d").date()
    b = datetime.strptime(until, "%Y-%m-%d").date()
    y, m = a.year, a.month
    end_y, end_m = b.year, b.month
    out: list[tuple[str, str]] = []
    while (y, m) <= (end_y, end_m):
        first = date(y, m, 1)
        label = first.strftime("%b %Y")
        out.append((first.isoformat(), label))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _parse_submission_timestamp_ms(sub: dict[str, Any]) -> int | None:
    for key in (
        "submittedAt",
        "submissionDate",
        "dateAdded",
        "createdAt",
        "updatedAt",
    ):
        raw = sub.get(key)
        if raw is None or raw == "":
            continue
        ms = _parse_contact_date_added_ms(raw)
        if ms is not None:
            return ms
    return None


def _submission_submitted_utc_date_str(sub: dict[str, Any]) -> str | None:
    ms = _parse_submission_timestamp_ms(sub)
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _iter_submission_label_value_pairs(sub: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten common GHL form submission shapes to (label_or_key, text)."""
    pairs: list[tuple[str, str]] = []

    def add_from_mapping(m: Any, key_as_label: bool = True) -> None:
        if not isinstance(m, dict):
            return
        for k, v in m.items():
            label = str(k) if key_as_label else ""
            text = _ghl_custom_field_value_to_str(v)
            if text:
                pairs.append((label, text))

    add_from_mapping(sub.get("others"))
    add_from_mapping(sub.get("formData"))
    add_from_mapping(sub.get("data"))

    for arr_key in ("fields", "customFields", "customData", "answers"):
        arr = sub.get(arr_key)
        if not isinstance(arr, list):
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            label = (
                item.get("name")
                or item.get("label")
                or item.get("fieldName")
                or item.get("title")
                or item.get("id")
                or item.get("fieldId")
                or ""
            )
            val = item.get("value")
            if val is None:
                val = item.get("fieldValue")
            text = _ghl_custom_field_value_to_str(val)
            if text:
                pairs.append((str(label), text))

    return pairs


def _field_label_suggests_hear_about_source(label: str) -> bool:
    fp = _field_name_fingerprint(label)
    if fp in _FORM_SOURCE_QUESTION_FINGERPRINTS:
        return True
    if "hearabout" in fp or "heardabout" in fp:
        return True
    if "howdidyouhear" in fp:
        return True
    if "where" in fp and "hear" in fp:
        return True
    if "findus" in fp or "foundus" in fp:
        return True
    return False


def submission_hear_about_answer(sub: dict[str, Any]) -> str:
    """Best-effort answer for how-they-heard, from submission payload."""
    pairs = _iter_submission_label_value_pairs(sub)
    for label, text in pairs:
        if _field_label_suggests_hear_about_source(label):
            return text
    env_fp = (os.getenv("GHL_FORM_SOURCE_FIELD_FINGERPRINT") or "").strip().casefold()
    if env_fp:
        for label, text in pairs:
            if _field_name_fingerprint(label).casefold() == env_fp:
                return text
    for label, text in pairs:
        fp = _field_name_fingerprint(label)
        if fp in _FORM_FIELD_LABEL_SKIP_FINGERPRINTS:
            continue
        if "@" in text or len(text) > 120:
            continue
        if _classify_word_of_mouth_vs_google(text):
            return text
    return ""


def _classify_word_of_mouth_vs_google(answer: str) -> str | None:
    """
    Bucket form answers into **Google** vs **Word of mouth** (family, friend, etc.).

    Returns ``Google``, ``Word of mouth``, or None if neither matches.
    """
    v = (answer or "").strip().casefold()
    if not v:
        return None
    if "google" in v:
        return "Google"
    wom_phrases = (
        "family member",
        "word of mouth",
        "neighbor",
        "neighbour",
        "colleague",
        "coworker",
        "co-worker",
        "referral",
        "referred",
        "existing patient",
        "another patient",
        "patient referral",
        "family/friend",
        "friend or family",
    )
    for phrase in wom_phrases:
        if phrase in v:
            return "Word of mouth"
    if v in ("friend", "family", "wom", "word of mouth"):
        return "Word of mouth"
    return None


def fetch_forms(location_id: str | None = None) -> list[dict[str, Any]]:
    """GET ``/forms/`` for the location (ids and names)."""
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    data = _ghl_get_json("/forms/", {"locationId": location_id})
    forms = data.get("forms")
    if forms is None and isinstance(data.get("form"), dict):
        forms = [data["form"]]
    if forms is None:
        inner = data.get("data")
        forms = inner if isinstance(inner, list) else []
    if not isinstance(forms, list):
        forms = []
    return [f for f in forms if isinstance(f, dict)]


def resolve_form_id_by_name(
    form_name: str,
    location_id: str | None = None,
    *,
    env_form_id_var: str = "GHL_DC_APPOINTMENT_FORM_ID",
) -> str:
    env_id = (os.getenv(env_form_id_var) or "").strip()
    if env_id:
        return env_id
    want = _normalize_field_name(form_name)
    for f in fetch_forms(location_id):
        nm = f.get("name") or f.get("title") or ""
        if _normalize_field_name(str(nm)) == want:
            fid = f.get("id")
            if fid:
                return str(fid)
    raise ValueError(
        f"No form matching name {form_name!r}. Set {env_form_id_var} in .env to the form id."
    )


def fetch_form_submissions_all(
    form_id: str,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = 500,
    stop_when_page_oldest_before_ms: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Paginate GET ``/forms/submissions`` for one form.

    The API accepts ``locationId``, ``formId``, ``limit``, and ``page`` (1-based);
    ``skip`` / ``startAfterId`` are rejected with 422.

    Results are newest-first. If ``stop_when_page_oldest_before_ms`` is set (e.g. range
    start), pagination stops once the oldest submission on a page is strictly before
    that instant (no later page can include newer rows).

    Returns (submissions, truncated) where truncated is True if ``max_pages`` hit.
    """
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    fid = (form_id or "").strip()
    if not fid:
        raise ValueError("form_id is required")

    out: list[dict[str, Any]] = []
    truncated = False
    for page in range(1, max_pages + 1):
        data = _ghl_get_json(
            "/forms/submissions",
            {
                "locationId": location_id,
                "formId": fid,
                "limit": page_limit,
                "page": page,
            },
        )
        batch = data.get("submissions")
        if batch is None and isinstance(data.get("submission"), dict):
            batch = [data["submission"]]
        if batch is None:
            inner = data.get("data")
            batch = inner if isinstance(inner, list) else []
        if not isinstance(batch, list):
            batch = []
        dict_batch = [s for s in batch if isinstance(s, dict)]
        out.extend(dict_batch)

        page_ms = [
            m
            for s in dict_batch
            if (m := _parse_submission_timestamp_ms(s)) is not None
        ]
        if (
            stop_when_page_oldest_before_ms is not None
            and page_ms
            and min(page_ms) < stop_when_page_oldest_before_ms
        ):
            break

        meta = data.get("meta")
        next_p = meta.get("nextPage") if isinstance(meta, dict) else None
        if next_p is None:
            break
    else:
        truncated = True

    return out, truncated


def fetch_dc_appointment_form_source_monthly(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    form_name: str = DC_APPOINTMENT_FORM_NAME_DEFAULT,
) -> dict[str, Any]:
    """
    **DC Appointment Form** submissions in [since, until] (UTC calendar days),
    grouped by submission month. Each row counts **Word of mouth** vs **Google**
    based on the “how did you hear” answer (see :func:`submission_hear_about_answer`).
    """
    start_ms, end_ms = _inclusive_utc_range_ms(since, until)
    form_id = resolve_form_id_by_name(form_name, location_id)
    submissions, truncated = fetch_form_submissions_all(
        form_id,
        location_id=location_id,
        stop_when_page_oldest_before_ms=start_ms,
    )

    per_month: dict[str, dict[str, int]] = {}
    by_bucket: dict[str, int] = {"Word of mouth": 0, "Google": 0}
    missing_submission_date = 0
    out_of_range = 0
    unclassified_in_range = 0

    for sub in submissions:
        d = _submission_submitted_utc_date_str(sub)
        sub_ms = _parse_submission_timestamp_ms(sub)
        if not d or sub_ms is None:
            missing_submission_date += 1
            continue
        try:
            day = date.fromisoformat(d)
        except ValueError:
            missing_submission_date += 1
            continue
        if not (start_ms <= sub_ms <= end_ms):
            out_of_range += 1
            continue
        ans = submission_hear_about_answer(sub)
        bucket = _classify_word_of_mouth_vs_google(ans)
        if bucket is None:
            unclassified_in_range += 1
            continue
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        ym = f"{day.year:04d}-{day.month:02d}"
        m_bucket = per_month.setdefault(
            ym, {"Word of mouth": 0, "Google": 0}
        )
        m_bucket[bucket] += 1

    monthly: list[dict[str, Any]] = []
    for month_start, month_label in _month_periods_inclusive(since, until):
        ym = month_start[:7]
        vals = per_month.get(ym, {"Word of mouth": 0, "Google": 0})
        w = int(vals["Word of mouth"])
        g = int(vals["Google"])
        monthly.append(
            {
                "month_start": month_start,
                "month_label": month_label,
                "word_of_mouth": w,
                "google": g,
            }
        )

    return {
        "since": since,
        "until": until,
        "form_id": form_id,
        "form_name": form_name,
        "monthly": monthly,
        "by_bucket": by_bucket,
        "truncated_pages": truncated,
        "submissions_loaded": len(submissions),
        "missing_submission_date": missing_submission_date,
        "unclassified_in_range": unclassified_in_range,
        "submissions_out_of_range": out_of_range,
    }


def fetch_committed_true_contacts(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    committed_field_name: str = COMMITTED_FIELD_NAME,
) -> dict[str, Any]:
    """
    Contacts where custom field ``Committed`` is true and ``dateAdded`` falls in
    the inclusive UTC range [since, until] (YYYY-MM-DD).
    """
    committed_field_id = resolve_committed_custom_field_id(
        location_id, field_name=committed_field_name
    )
    if not committed_field_id:
        raise ValueError(
            "Could not resolve the 'Committed' custom field. "
            "Set GHL_COMMITTED_FIELD_ID in .env."
        )
    start_ms, end_ms = _inclusive_utc_range_ms(since, until)

    truncated = False
    combined: dict[str, dict[str, Any]] = {}
    for val in ("TRUE", "true", "True"):
        raw, more = search_contacts_by_custom_field_equals(
            committed_field_id, val, location_id=location_id
        )
        truncated = truncated or more
        for c in raw:
            cid = str(c.get("id") or "")
            if cid:
                combined[cid] = c

    filtered: list[dict[str, Any]] = []
    for c in combined.values():
        added_ms = _parse_contact_date_added_ms(
            c.get("dateAdded") or c.get("createdAt")
        )
        if added_ms is None or not (start_ms <= added_ms <= end_ms):
            continue
        filtered.append(c)

    filtered.sort(key=_contact_sort_key, reverse=True)
    return {
        "since": since,
        "until": until,
        "committed_field_id": committed_field_id,
        "contacts": filtered,
        "truncated_pages": truncated,
    }


def fetch_committed_yes_contacts(
    *,
    location_id: str | None = None,
    committed_field_name: str = COMMITTED_FIELD_NAME,
    max_pages: int = 100,
) -> dict[str, Any]:
    """
    All contacts where the Committed custom field equals **Yes** (no date filter).

    Paginates ``/contacts/search`` until results end or ``max_pages`` is hit per
    value variant. Deduplicates by contact id.

    Returns:
        {
            "committed_field_id": str,
            "membership_level_field_id": str,  # "" if not resolved
            "contacts": list of contact dicts,
            "truncated_pages": bool,
        }
    """
    committed_field_id = resolve_committed_custom_field_id(
        location_id, field_name=committed_field_name
    )
    if not committed_field_id:
        raise ValueError(
            "Could not resolve the Committed custom field. "
            "Set GHL_COMMITTED_FIELD_ID in .env."
        )
    mid = resolve_membership_level_custom_field_id(location_id)

    truncated = False
    combined: dict[str, dict[str, Any]] = {}
    for val in ("Yes", "yes", "YES"):
        raw, more = search_contacts_by_custom_field_equals(
            committed_field_id,
            val,
            location_id=location_id,
            max_pages=max_pages,
        )
        truncated = truncated or more
        for c in raw:
            cid = str(c.get("id") or "")
            if cid:
                combined[cid] = c

    contacts = sorted(combined.values(), key=_contact_sort_key, reverse=True)
    return {
        "committed_field_id": committed_field_id,
        "membership_level_field_id": mid or "",
        "contacts": contacts,
        "truncated_pages": truncated,
    }


def _search_contacts_custom_field_date_window(
    field_id: str,
    since: str,
    until: str,
    *,
    location_id: str,
    page_limit: int = 100,
    max_pages: int = _GHL_CONTACT_SEARCH_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool, int]:
    """
    One ``/contacts/search`` window for a DATE custom field (inclusive YYYY-MM-DD).

    GHL rejects ``page * pageLimit > 10_000``; keep ``max_pages`` at 100 when
    ``page_limit`` is 100.
    """
    filters = [
        {
            "field": f"customFields.{field_id}",
            "operator": "range",
            "value": {"gte": since, "lte": until},
        }
    ]

    out: list[dict[str, Any]] = []
    total_reported = 0
    truncated = False
    for p in range(1, max_pages + 1):
        payload: dict[str, Any] = {
            "locationId": location_id,
            "pageLimit": page_limit,
            "page": p,
            "filters": filters,
        }
        data = _ghl_post_json("/contacts/search", payload)
        batch = data.get("contacts")
        if batch is None:
            inner = data.get("data")
            batch = inner if isinstance(inner, list) else []
        if not isinstance(batch, list):
            batch = []
        if p == 1:
            tr = data.get("total")
            if isinstance(tr, int):
                total_reported = tr
            elif isinstance(tr, str) and tr.isdigit():
                total_reported = int(tr)

        out.extend(batch)
        if not batch or len(batch) < page_limit:
            break
    else:
        truncated = True

    return out, truncated, total_reported


def search_contacts_custom_field_date_range(
    field_id: str,
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    page_limit: int = 100,
    max_pages: int = _GHL_CONTACT_SEARCH_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool, int]:
    """
    Contacts whose DATE custom field is in ``[since, until]`` inclusive (YYYY-MM-DD).

    Uses POST ``/contacts/search`` with operator ``range`` and
    ``value: {"gte": since, "lte": until}``. Pagination uses the ``page`` field
    (1-based).

    Splits multi-day windows into daily chunks so wide ranges do not hit GHL's
    ``page * pageLimit <= 10_000`` cap (HTTP 400 on deep pages).

    Returns:
        (contacts, truncated_pages, total_reported)
        ``total_reported`` sums API ``total`` counts across daily chunks.
    """
    if location_id and location_id.strip():
        location_id = location_id.strip()
    else:
        location_id = _location_id()
    if not location_id:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    fid = (field_id or "").strip()
    if not fid:
        raise ValueError("field_id is required")

    combined: dict[str, dict[str, Any]] = {}
    truncated = False
    total_reported = 0
    skipped_days = 0
    for chunk_index, (chunk_since, chunk_until) in enumerate(
        _calendar_day_chunks(since, until)
    ):
        if chunk_index > 0:
            time.sleep(0.2)
        last_exc: RuntimeError | None = None
        for attempt in range(3):
            try:
                batch, part_truncated, part_total = (
                    _search_contacts_custom_field_date_window(
                        fid,
                        chunk_since,
                        chunk_until,
                        location_id=location_id,
                        page_limit=page_limit,
                        max_pages=max_pages,
                    )
                )
                last_exc = None
                break
            except RuntimeError as exc:
                last_exc = exc
                if attempt + 1 < 3:
                    time.sleep(0.6 * (attempt + 1))
        if last_exc is not None:
            skipped_days += 1
            truncated = True
            continue

        truncated = truncated or part_truncated
        total_reported += part_total
        for contact in batch:
            cid = str(contact.get("id") or "")
            if cid:
                combined[cid] = contact

    if skipped_days:
        truncated = True

    return list(combined.values()), truncated, total_reported


def fetch_signup_date_range_committed_yes_contacts(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any]:
    """
    Contacts whose **Sign Up Date** custom field is in ``[since, until]`` (inclusive,
    YYYY-MM-DD) and whose **Committed?** field equals **Yes** (case-insensitive).

    Uses :func:`search_contacts_custom_field_date_range` on the sign-up field, then
    filters in memory on the committed field value.

    Raises:
        ValueError: If sign-up or committed custom field id cannot be resolved.

    Returns:
        Dict with ``contacts`` (filtered list), field ids, ``truncated_pages``,
        ``total_reported`` from the sign-up date search (before committed filter),
        ``signup_matches_loaded``, and ``excluded_not_committed_yes``.
    """
    sid = resolve_sign_up_date_custom_field_id(location_id)
    if not sid:
        raise ValueError(
            "Could not resolve the Sign Up Date custom field. "
            "Set GHL_SIGN_UP_DATE_FIELD_ID in .env."
        )
    committed_id = resolve_committed_custom_field_id(location_id)
    if not committed_id:
        raise ValueError(
            "Could not resolve the Committed custom field. "
            "Set GHL_COMMITTED_FIELD_ID in .env."
        )
    mid = resolve_membership_level_custom_field_id(location_id)

    contacts, truncated, total_reported = search_contacts_custom_field_date_range(
        sid,
        since,
        until,
        location_id=location_id,
    )

    filtered: list[dict[str, Any]] = []
    excluded_not_yes = 0
    for c in contacts:
        raw = contact_custom_field_value(c, committed_id).strip()
        if raw.casefold() != "yes":
            excluded_not_yes += 1
            continue
        filtered.append(c)

    filtered.sort(key=_contact_sort_key, reverse=True)
    return {
        "since": since,
        "until": until,
        "sign_up_date_field_id": sid,
        "committed_field_id": committed_id,
        "membership_level_field_id": mid or "",
        "contacts": filtered,
        "truncated_pages": truncated,
        "total_reported": total_reported,
        "signup_matches_loaded": len(contacts),
        "excluded_not_committed_yes": excluded_not_yes,
    }


def fetch_contacts_cancellation_date_in_range(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any]:
    """
    All contacts whose cancellation date custom field falls in ``[since, until]``
    (inclusive calendar dates, YYYY-MM-DD).

    Resolves **Membership Cancellation Date** (or ``GHL_CANCELLATION_DATE_FIELD_ID``).
    """
    cid = resolve_cancellation_date_custom_field_id(location_id)
    if not cid:
        raise ValueError(
            "Could not resolve the cancellation date custom field. "
            "Set GHL_CANCELLATION_DATE_FIELD_ID in .env."
        )
    mid = resolve_membership_level_custom_field_id(location_id)
    contacts, truncated, total_reported = search_contacts_custom_field_date_range(
        cid,
        since,
        until,
        location_id=location_id,
    )
    contacts.sort(key=_contact_sort_key, reverse=True)
    return {
        "since": since,
        "until": until,
        "cancellation_field_id": cid,
        "membership_level_field_id": mid or "",
        "contacts": contacts,
        "truncated_pages": truncated,
        "total_reported": total_reported,
    }


def fetch_cancellation_date_range_membership_cancelled_true_contacts(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any]:
    """
    Contacts whose **Membership Cancellation Date** is in ``[since, until]`` (inclusive)
    and whose **Membership Cancelled** custom field is truthy (**true** / **yes** /
    **1**, case-insensitive).

    Raises:
        ValueError: If cancellation date field or membership-cancelled field cannot
        be resolved.
    """
    base = fetch_contacts_cancellation_date_in_range(since, until, location_id=location_id)
    mc_id = resolve_membership_cancelled_custom_field_id(location_id)
    if not mc_id:
        raise ValueError(
            "Could not resolve the Membership Cancelled custom field. "
            "Set GHL_MEMBERSHIP_CANCELLED_FIELD_ID in .env."
        )
    contacts: list[dict[str, Any]] = base["contacts"]
    filtered: list[dict[str, Any]] = []
    excluded = 0
    for c in contacts:
        raw = contact_custom_field_value(c, mc_id).strip()
        if raw.casefold() not in ("true", "yes", "1", "y"):
            excluded += 1
            continue
        filtered.append(c)
    filtered.sort(key=_contact_sort_key, reverse=True)
    return {
        "since": base["since"],
        "until": base["until"],
        "cancellation_field_id": base["cancellation_field_id"],
        "membership_cancelled_field_id": mc_id,
        "membership_level_field_id": base.get("membership_level_field_id") or "",
        "contacts": filtered,
        "truncated_pages": base["truncated_pages"],
        "total_reported": int(base.get("total_reported") or 0),
        "cancellation_matches_loaded": len(contacts),
        "excluded_not_cancelled_true": excluded,
    }


def parse_membership_cancellation_date(raw: str) -> date | None:
    """Parse **Membership Cancellation Date** style values to a calendar date."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def fetch_cancellation_counts_by_month(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any]:
    """
    Contacts with **Membership Cancellation Date** in ``[since, until]`` (inclusive),
    aggregated by **calendar month of that cancellation date** (same month grid as
    :func:`fetch_hear_about_wom_google_monthly_by_date_added` for the same window).
    """
    base = fetch_contacts_cancellation_date_in_range(since, until, location_id=location_id)
    fid = base["cancellation_field_id"]
    contacts = base["contacts"]
    per_month: dict[str, int] = {}
    unparseable = 0
    for c in contacts:
        raw = contact_custom_field_value(c, fid)
        d = parse_membership_cancellation_date(raw)
        if d is None:
            unparseable += 1
            continue
        ym = f"{d.year:04d}-{d.month:02d}"
        per_month[ym] = per_month.get(ym, 0) + 1

    monthly: list[dict[str, Any]] = []
    for month_start, month_label in _month_periods_inclusive(since, until):
        ym = month_start[:7]
        monthly.append(
            {
                "month_start": month_start,
                "month_label": month_label,
                "cancellations": int(per_month.get(ym, 0)),
            }
        )

    return {
        "since": since,
        "until": until,
        "cancellation_field_id": fid,
        "monthly": monthly,
        "contacts_loaded": len(contacts),
        "unparseable_cancellation_dates": unparseable,
        "truncated_pages": base["truncated_pages"],
        "total_reported": int(base.get("total_reported") or 0),
    }


def _event_date_added_ymd(event: dict[str, Any]) -> str | None:
    return _event_timestamp_ymd(event.get("dateAdded"))


def _event_start_time_ymd(event: dict[str, Any]) -> str | None:
    return _event_timestamp_ymd(event.get("startTime"))


def _event_timestamp_ymd(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if text.isdigit():
        dt = datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
        return dt.date().isoformat()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


@dataclass(frozen=True)
class CalendarFunnelCounts:
    bookings: int
    meetings: int
    calendar_api_errors: int


DISCOVERY_CALL_CALENDAR_IDS = frozenset(
    {
        "qja10C2jumapTz3SYfm6",
        "1iZockuvSA2XXTgotgPt",
        "8GkEgvCZuwmB0CLMSRCN",
        "DqCs4ObnGQ4ln3y79dzK",
        "DtpoxOu9BzWYlUFYUL9t",
        "EpmLirh2HfRdGdea3hDJ",
        "Hd6SnZY1XHxEwDlBLUeR",
        "NB9LY4dVs46qcRipazXf",
        "Nh5QeId7mOTJC0iNuurI",
        "OVtjGmf8UJVRXkGNxuuP",
        "RotbRQw8mJruB2jI3dCz",
        "XnKWvTEUGHksSNMF14uW",
        "Y4Koluh4AEZTEZW6BhEg",
        "Yuk70OiiCvjbbWX9myWx",
        "eXQ31j1E0L7Ge6OCMPRp",
        "jiAljaBAjymPM6Tj9aPX",
        "oMuSGis05uOyYuZdVymi",
        "sTjhbFgjjytqHiJHdVO3",
        "tjOTr2nk5qqfKCBx7Eit",
        "vqsYIK9VZZvgZb8vrhP8",
        "xf47OvgHxUd7dJVN7M4t",
        "4HmoxX3GIyKy5yQfl1s2",
        "8eCfJpto3VFl86P9MHAD",
        "AnyJeUUWpJNW53j1MqnV",
        "Hvr63hIMIuWT8f1Vewwj",
        "PqQSqi0C6kujYZud0slp",
        "ZLT2UxrQ39leknYZYBuq",
    }
)

DISCOVERY_CALL_APPOINTMENT_STATUSES = frozenset(
    {"confirmed", "showed", "completed", "active", "new"}
)

_DISCOVERY_CALL_CANCELLED_STATUSES = frozenset(
    {"cancelled", "canceled", "no_show", "noshow", "invalid"}
)


def discovery_call_calendar_ids() -> frozenset[str]:
    """Discovery-call calendar IDs (override via ``GHL_DISCOVERY_CALL_CALENDAR_IDS``)."""
    raw = os.getenv("GHL_DISCOVERY_CALL_CALENDAR_IDS", "").strip()
    if raw:
        return frozenset(part.strip() for part in raw.split(",") if part.strip())
    return DISCOVERY_CALL_CALENDAR_IDS


def paid_media_channel_for_hear_about(raw: str) -> str | None:
    """Map hear-about text to ``google`` or ``meta`` for paid-media DC attribution."""
    text = (raw or "").strip()
    if not text:
        return None
    lower = text.casefold()
    if lower in {"facebook", "instagram"}:
        return "meta"
    if classify_hear_about_wom_vs_google(text) == "Google":
        return "google"
    if "google" in lower:
        return "google"
    if "facebook" in lower or "instagram" in lower or "fb" in lower:
        return "meta"
    return None


def _discovery_call_meeting_ok(event: dict[str, Any]) -> bool:
    status = (
        event.get("appointmentStatus") or event.get("appoinmentStatus") or ""
    ).casefold()
    if status in _DISCOVERY_CALL_CANCELLED_STATUSES:
        return False
    if not status:
        return True
    return status in DISCOVERY_CALL_APPOINTMENT_STATUSES


def _fetch_location_calendars(
    location_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(calendars, api_errors)`` for a location."""
    try:
        data = _ghl_get_json("/calendars/", params={"locationId": location_id}, timeout=60)
    except RuntimeError:
        return [], 1
    calendars = data.get("calendars")
    if not isinstance(calendars, list):
        calendars = []
    return calendars, 0


def _calendar_allowed_days(since: str, until: str) -> set[str]:
    since_date = date.fromisoformat(since)
    until_date = date.fromisoformat(until)
    return {
        (since_date + timedelta(days=offset)).isoformat()
        for offset in range((until_date - since_date).days + 1)
    }


def _calendar_fetch_window_ms(since: str, until: str) -> tuple[str, str]:
    since_date = date.fromisoformat(since)
    until_date = date.fromisoformat(until)
    window_start = datetime.combine(
        since_date - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc
    )
    window_end = datetime.combine(
        until_date + timedelta(days=180), datetime.max.time(), tzinfo=timezone.utc
    )
    return str(int(window_start.timestamp() * 1000)), str(int(window_end.timestamp() * 1000))


_calendar_events_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]], int]] = {}
_CALENDAR_EVENTS_CACHE_TTL_SEC = 300


def _fetch_calendar_events_deduped(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    calendar_ids: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Non-deleted calendar events in the scan window, deduped by event id.

    When ``calendar_ids`` is set, only those calendars are queried (faster).

    Cached in-process for five minutes so command strip, CRM funnel, and
    conversion drivers share one calendar pass per date range.
    """
    if location_id and location_id.strip():
        loc = location_id.strip()
    else:
        loc = _location_id()
    if not loc:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    cal_key = tuple(sorted(calendar_ids)) if calendar_ids else ()
    cache_key = (since, until, loc, cal_key)
    now = time.time()
    cached = _calendar_events_cache.get(cache_key)
    if cached and now - cached[0] < _CALENDAR_EVENTS_CACHE_TTL_SEC:
        return cached[1], cached[2]

    start_ms, end_ms = _calendar_fetch_window_ms(since, until)
    headers = _request_headers()
    api_errors = 0
    if calendar_ids:
        calendars = [{"id": calendar_id} for calendar_id in sorted(calendar_ids)]
    else:
        calendars, api_errors = _fetch_location_calendars(loc)

    seen_ids: set[str] = set()
    events: list[dict[str, Any]] = []

    for calendar in calendars:
        calendar_id = calendar.get("id")
        if not calendar_id:
            continue
        events_response = requests.get(
            f"{BASE_URL}/calendars/events",
            params={
                "locationId": loc,
                "calendarId": calendar_id,
                "startTime": start_ms,
                "endTime": end_ms,
            },
            headers=headers,
            timeout=90,
        )
        if not events_response.ok:
            api_errors += 1
            continue
        for event in events_response.json().get("events") or []:
            event_id = event.get("id")
            if not event_id or str(event_id) in seen_ids:
                continue
            seen_ids.add(str(event_id))
            if event.get("deleted"):
                continue
            events.append(event)

    _calendar_events_cache[cache_key] = (now, events, api_errors)
    return events, api_errors


def count_calendar_funnel_events(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> CalendarFunnelCounts:
    """
    Count calendar funnel events in ``[since, until]`` inclusive (YYYY-MM-DD).

    - **Bookings** — non-deleted events whose ``dateAdded`` falls in range.
    - **Meetings** — non-deleted events whose ``startTime`` falls in range.

    Uses one calendar scan with a wide ``startTime`` fetch window so future-dated
    bookings are included.
    """
    if location_id and location_id.strip():
        loc = location_id.strip()
    else:
        loc = _location_id()
    if not loc:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    allowed_days = _calendar_allowed_days(since, until)
    events, api_errors = _fetch_calendar_events_deduped(
        since, until, location_id=loc
    )

    bookings = 0
    meetings = 0
    for event in events:
        if _event_date_added_ymd(event) in allowed_days:
            bookings += 1
        if _event_start_time_ymd(event) in allowed_days:
            meetings += 1

    return CalendarFunnelCounts(
        bookings=bookings,
        meetings=meetings,
        calendar_api_errors=api_errors,
    )


def _calendar_events_in_range(
    since: str,
    until: str,
    *,
    day_from_event,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Non-deleted calendar events whose ``day_from_event(event)`` falls in range.

    Scans events with ``startTime`` from seven days before ``since`` through
    180 days after ``until``.
    """
    if location_id and location_id.strip():
        loc = location_id.strip()
    else:
        loc = _location_id()
    if not loc:
        raise ValueError("Set GHL_LOCATION_ID in .env")

    allowed_days = _calendar_allowed_days(since, until)
    events, api_errors = _fetch_calendar_events_deduped(
        since, until, location_id=loc
    )
    matched = [
        event
        for event in events
        if day_from_event(event) in allowed_days
    ]
    return matched, api_errors


def _calendar_bookings_in_date_added_range(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Non-deleted calendar events whose ``dateAdded`` falls in range."""
    return _calendar_events_in_range(
        since,
        until,
        day_from_event=_event_date_added_ymd,
        location_id=location_id,
    )


def _calendar_meetings_in_start_time_range(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Non-deleted calendar events whose ``startTime`` falls in range."""
    return _calendar_events_in_range(
        since,
        until,
        day_from_event=_event_start_time_ymd,
        location_id=location_id,
    )


def count_calendar_bookings_by_date_added(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
) -> tuple[int, int]:
    """
    Count non-deleted calendar events whose ``dateAdded`` falls on days in
    ``[since, until]`` inclusive (YYYY-MM-DD).

    Returns:
        (booking_count, calendar_api_errors)
    """
    bookings, api_errors = _calendar_bookings_in_date_added_range(
        since, until, location_id=location_id
    )
    return len(bookings), api_errors


def _normalize_hear_about_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "(Not set)"
    if text.casefold().startswith("word of mouth"):
        return "WOM"
    if text.casefold().startswith("3rd party"):
        return "3rd party"
    return text


def _hear_about_rows_from_counter(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"source": label, "count": int(count)}
        for label, count in counter.most_common()
    ]


def fetch_contact_by_id(
    contact_id: str,
    *,
    location_id: str | None = None,
) -> dict[str, Any] | None:
    """Load a single contact by id (returns None if the API call fails)."""
    if not (contact_id or "").strip():
        return None
    if location_id and location_id.strip():
        loc = location_id.strip()
    else:
        loc = _location_id()
    if not loc:
        raise ValueError("Set GHL_LOCATION_ID in .env")
    try:
        data = _ghl_get_json(
            f"/contacts/{contact_id.strip()}",
            params={"locationId": loc},
            timeout=60,
        )
    except (RuntimeError, requests.RequestException):
        return None
    contact = data.get("contact")
    if isinstance(contact, dict):
        return contact
    if isinstance(data, dict) and data.get("id"):
        return data
    return None


def fetch_contacts_by_ids(
    contact_ids: set[str] | list[str],
    *,
    location_id: str | None = None,
    max_workers: int = 8,
) -> dict[str, dict[str, Any] | None]:
    """Load many contacts in parallel (deduped by id)."""
    unique_ids = sorted({str(cid).strip() for cid in contact_ids if str(cid).strip()})
    if not unique_ids:
        return {}

    cache: dict[str, dict[str, Any] | None] = {}
    workers = max(1, min(max_workers, len(unique_ids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_contact_by_id, cid, location_id=location_id): cid
            for cid in unique_ids
        }
        for future in as_completed(futures):
            cid = futures[future]
            try:
                cache[cid] = future.result()
            except Exception:
                cache[cid] = None
    return cache


def _aggregate_events_by_hear_about(
    events: list[dict[str, Any]],
    *,
    hear_id: str,
    contact_cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    by_source: Counter[str] = Counter()
    missing_contact_link = 0
    contact_lookup_failures = 0

    for event in events:
        contact_id = event.get("contactId")
        if not contact_id:
            missing_contact_link += 1
            by_source["(No contact linked)"] += 1
            continue
        cid = str(contact_id)
        contact = contact_cache.get(cid)
        if contact is None and cid not in contact_cache:
            contact = fetch_contact_by_id(cid)
            contact_cache[cid] = contact
        if not contact:
            contact_lookup_failures += 1
            by_source["(Contact not found)"] += 1
            continue
        label = _normalize_hear_about_label(contact_custom_field_value(contact, hear_id))
        by_source[label] += 1

    return {
        "rows": _hear_about_rows_from_counter(by_source),
        "total": len(events),
        "missing_contact_link": missing_contact_link,
        "contact_lookup_failures": contact_lookup_failures,
    }


def fetch_discovery_call_meetings_monthly_by_channel(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    calendar_ids: frozenset[str] | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Discovery-call **meetings** (``startTime`` in range) on configured calendars.

    Returns monthly Google / Meta / unallocated counts using hear-about on the
    linked contact (same paid-media mapping as the Digital Channel Dashboard).
    """
    cal_ids = calendar_ids or discovery_call_calendar_ids()
    hear_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    allowed_days = _calendar_allowed_days(since, until)
    events, api_errors = _fetch_calendar_events_deduped(
        since, until, location_id=location_id, calendar_ids=cal_ids
    )

    meeting_events = [
        event
        for event in events
        if _event_start_time_ymd(event) in allowed_days
        and _discovery_call_meeting_ok(event)
    ]

    contact_ids = {
        str(event["contactId"]) for event in meeting_events if event.get("contactId")
    }
    contact_cache = fetch_contacts_by_ids(contact_ids, location_id=location_id)

    per_month: dict[str, dict[str, int]] = {}
    missing_contact_link = 0
    unattributed = 0

    for event in meeting_events:
        day = _event_start_time_ymd(event)
        if not day:
            continue
        ym = day[:7]
        bucket = per_month.setdefault(ym, {"google": 0, "meta": 0, "unallocated": 0})

        contact_id = event.get("contactId")
        if not contact_id:
            missing_contact_link += 1
            bucket["unallocated"] += 1
            continue

        contact = contact_cache.get(str(contact_id))
        if not contact:
            bucket["unallocated"] += 1
            continue

        channel = paid_media_channel_for_hear_about(
            contact_custom_field_value(contact, hear_id)
        )
        if channel == "google":
            bucket["google"] += 1
        elif channel == "meta":
            bucket["meta"] += 1
        else:
            unattributed += 1
            bucket["unallocated"] += 1

    monthly: list[dict[str, Any]] = []
    for month_start, month_label in _month_periods_inclusive(since, until):
        ym = month_start[:7]
        vals = per_month.get(ym, {"google": 0, "meta": 0, "unallocated": 0})
        monthly.append(
            {
                "month_start": month_start,
                "month_label": month_label,
                "google": int(vals["google"]),
                "meta": int(vals["meta"]),
                "unallocated": int(vals["unallocated"]),
            }
        )

    return {
        "since": since,
        "until": until,
        "calendar_ids": sorted(cal_ids),
        "calendar_count": len(cal_ids),
        "field_id": hear_id,
        "field_name": field_name,
        "meetings_total": len(meeting_events),
        "monthly": monthly,
        "calendar_api_errors": api_errors,
        "missing_contact_link": missing_contact_link,
        "unattributed_hear_about": unattributed,
    }


def fetch_bookings_and_meetings_by_hear_about_us(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Bookings (``dateAdded``) and meetings (``startTime``) in range, grouped by
    hear-about source — one calendar scan and one batched contact load.
    """
    hear_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    allowed_days = _calendar_allowed_days(since, until)
    events, api_errors = _fetch_calendar_events_deduped(
        since, until, location_id=location_id
    )

    booking_events = [
        event for event in events if _event_date_added_ymd(event) in allowed_days
    ]
    meeting_events = [
        event for event in events if _event_start_time_ymd(event) in allowed_days
    ]

    contact_ids = {
        str(event["contactId"])
        for event in booking_events + meeting_events
        if event.get("contactId")
    }
    contact_cache = fetch_contacts_by_ids(contact_ids, location_id=location_id)

    bookings = _aggregate_events_by_hear_about(
        booking_events,
        hear_id=hear_id,
        contact_cache=contact_cache,
    )
    meetings = _aggregate_events_by_hear_about(
        meeting_events,
        hear_id=hear_id,
        contact_cache=contact_cache,
    )

    return {
        "since": since,
        "until": until,
        "field_id": hear_id,
        "field_name": field_name,
        "calendar_api_errors": api_errors,
        "unique_contacts": len(contact_cache),
        "bookings": bookings,
        "meetings": meetings,
    }


def fetch_bookings_by_hear_about_us(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Calendar bookings (``dateAdded`` in range) grouped by the linked contact's
    **How did you hear about us?** custom field value.
    """
    hear_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    bookings, api_errors = _calendar_bookings_in_date_added_range(
        since, until, location_id=location_id
    )
    contact_ids = {str(event["contactId"]) for event in bookings if event.get("contactId")}
    contact_cache = fetch_contacts_by_ids(contact_ids, location_id=location_id)
    grouped = _aggregate_events_by_hear_about(
        bookings,
        hear_id=hear_id,
        contact_cache=contact_cache,
    )

    return {
        "since": since,
        "until": until,
        "field_id": hear_id,
        "field_name": field_name,
        "rows": grouped["rows"],
        "total_bookings": grouped["total"],
        "calendar_api_errors": api_errors,
        "missing_contact_link": grouped["missing_contact_link"],
        "contact_lookup_failures": grouped["contact_lookup_failures"],
        "unique_contacts": len(contact_cache),
    }


def fetch_meetings_by_hear_about_us(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Calendar meetings (``startTime`` in range) grouped by the linked contact's
    **How did you hear about us?** custom field value.
    """
    hear_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    meetings, api_errors = _calendar_meetings_in_start_time_range(
        since, until, location_id=location_id
    )
    contact_ids = {str(event["contactId"]) for event in meetings if event.get("contactId")}
    contact_cache = fetch_contacts_by_ids(contact_ids, location_id=location_id)
    grouped = _aggregate_events_by_hear_about(
        meetings,
        hear_id=hear_id,
        contact_cache=contact_cache,
    )

    return {
        "since": since,
        "until": until,
        "field_id": hear_id,
        "field_name": field_name,
        "rows": grouped["rows"],
        "total_meetings": grouped["total"],
        "calendar_api_errors": api_errors,
        "missing_contact_link": grouped["missing_contact_link"],
        "contact_lookup_failures": grouped["contact_lookup_failures"],
        "unique_contacts": len(contact_cache),
    }


def fetch_committed_yes_by_hear_about_us(
    since: str,
    until: str,
    *,
    location_id: str | None = None,
    field_name: str = HEAR_ABOUT_US_FIELD_NAME,
) -> dict[str, Any]:
    """
    Contacts with **Sign Up Date** in range and **Committed?** = **Yes**, grouped
    by **How did you hear about us?**
    """
    hear_id = resolve_hear_about_us_custom_field_id(location_id, field_name=field_name)
    cohort = fetch_signup_date_range_committed_yes_contacts(
        since, until, location_id=location_id
    )
    contacts = cohort["contacts"]

    by_source: Counter[str] = Counter()
    for contact in contacts:
        label = _normalize_hear_about_label(contact_custom_field_value(contact, hear_id))
        by_source[label] += 1

    return {
        "since": since,
        "until": until,
        "field_id": hear_id,
        "field_name": field_name,
        "rows": _hear_about_rows_from_counter(by_source),
        "total_committed": len(contacts),
        "truncated_pages": bool(cohort.get("truncated_pages")),
        "excluded_not_committed_yes": int(cohort.get("excluded_not_committed_yes") or 0),
        "signup_matches_loaded": int(cohort.get("signup_matches_loaded") or 0),
    }
