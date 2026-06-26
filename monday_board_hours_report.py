"""Fetch Monday board hours for a pay period and write detail + daily CSVs."""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from monday_client import (
    _pick_column_id_by_title,
    _pick_workflow_status_column_id,
    fetch_board_items_page,
    get_board_columns,
    parse_column_value,
    parse_status_column_value,
    resolve_board_ids_by_names,
    status_labels_from_settings,
)

DEFAULT_PAY_PERIOD_LENGTH_DAYS = 14
DEADLINE_FIELD = "Deadline/Worked On"
POSTING_FIELD = "Posting Schedule"
UPDATED_FIELD = "Last Updated"

STATUS_BUCKETS = {
    "Working On It": ["working on it"],
    "In Review": ["in review"],
    "Ready for Publishing": ["ready for publishing"],
    "Done/Published": ["done/published", "done", "published", "complete", "completed"],
}

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def pay_period_end(period_start: date, *, length_days: int = DEFAULT_PAY_PERIOD_LENGTH_DAYS) -> date:
    """Inclusive end date for a fixed-length pay period."""
    return period_start + timedelta(days=length_days - 1)


def _parse_hours(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return 0.0


def _parse_date_from_col(cv: dict) -> date | None:
    raw = parse_column_value(cv)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _in_range(d: date | None, *, period_start: date, period_end: date) -> bool:
    return d is not None and period_start <= d <= period_end


def _bucket_status(status: str) -> str | None:
    s = (status or "").strip().casefold()
    for bucket, needles in STATUS_BUCKETS.items():
        if any(n in s for n in needles):
            return bucket
    return None


def _fetch_all_items(board_id: str) -> list[dict]:
    board, cursor = fetch_board_items_page(board_id, limit=100)
    all_items: list[dict] = list((board.get("items_page") or {}).get("items") or [])
    while cursor:
        page_data, cursor = fetch_board_items_page(board_id, limit=100, cursor=cursor)
        batch = (page_data.get("items_page") or {}).get("items") or []
        all_items.extend(batch)
        if not batch:
            break
    return all_items


def load_board_hours_rows(
    board_name: str,
    *,
    period_start: date,
    period_end: date,
) -> list[dict]:
    board_map, missing = resolve_board_ids_by_names([board_name])
    if missing:
        raise ValueError(f"Board not found: {missing}")

    board_id = board_map[board_name]
    cols = get_board_columns(board_id)
    col_titles = {str(c["id"]): c.get("title") or "" for c in cols if c.get("id")}

    hours_col = _pick_column_id_by_title(cols, "total hours", "hours", "working hours")
    deadline_col = _pick_column_id_by_title(
        cols,
        "deadline/worked on",
        "final deadline",
        "worked on",
        "deadline",
    )
    posting_col = _pick_column_id_by_title(cols, "posting schedule")

    status_col_id = _pick_workflow_status_column_id(cols)
    status_labels: dict[str, str] = {}
    for col in cols:
        if str(col.get("id")) == status_col_id:
            status_labels = status_labels_from_settings(col.get("settings_str"))
            break

    rows: list[dict] = []
    for item in _fetch_all_items(board_id):
        values_by_id = {
            str(cv["id"]): cv for cv in (item.get("column_values") or []) if cv.get("id")
        }
        status = ""
        if status_col_id:
            status = parse_status_column_value(values_by_id.get(status_col_id, {}), status_labels)

        hours = 0.0
        if hours_col:
            hours_cv = values_by_id.get(hours_col, {})
            hours = _parse_hours(parse_column_value(hours_cv) or (hours_cv.get("text") or ""))

        deadline_dt = _parse_date_from_col(values_by_id.get(deadline_col or "", {}))
        posting_dt = _parse_date_from_col(values_by_id.get(posting_col or "", {}))

        updated_raw = (item.get("updated_at") or "")[:10]
        updated_dt: date | None = None
        if updated_raw:
            try:
                updated_dt = datetime.strptime(updated_raw, "%Y-%m-%d").date()
            except ValueError:
                pass

        in_period = _in_range(deadline_dt, period_start=period_start, period_end=period_end) or _in_range(
            posting_dt, period_start=period_start, period_end=period_end
        )
        if not in_period and hours > 0 and _in_range(
            updated_dt, period_start=period_start, period_end=period_end
        ):
            in_period = True
        if not in_period:
            continue

        if _in_range(deadline_dt, period_start=period_start, period_end=period_end):
            work_date = deadline_dt
            date_source = col_titles.get(deadline_col or "", DEADLINE_FIELD)
        elif _in_range(posting_dt, period_start=period_start, period_end=period_end):
            work_date = posting_dt
            date_source = col_titles.get(posting_col or "", POSTING_FIELD)
        else:
            work_date = updated_dt
            date_source = UPDATED_FIELD

        rows.append(
            {
                "task_name": (item.get("name") or "").strip(),
                "status": status or "No status",
                "working_hours": hours,
                "work_date": work_date.isoformat() if work_date else "",
                "date_source": date_source,
            }
        )

    rows.sort(key=lambda r: (r["work_date"], r["status"], -r["working_hours"], r["task_name"]))
    return rows


def load_board_rows(board_name: str) -> list[dict]:
    """CLI default pay period (May 31 – Jun 13, 2026)."""
    start = date(2026, 5, 31)
    end = date(2026, 6, 13)
    return load_board_hours_rows(board_name, period_start=start, period_end=end)


def write_csvs(
    prefix: str,
    rows: list[dict],
    *,
    period_start: date,
    period_end: date,
) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    start_s = period_start.isoformat()
    end_s = period_end.isoformat()
    detail_path = OUTPUT_DIR / f"{prefix}_{start_s}_to_{end_s}.csv"
    daily_path = OUTPUT_DIR / f"{prefix}_{start_s}_to_{end_s}_daily_summary.csv"

    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pay_period_start",
                "pay_period_end",
                "task_name",
                "status",
                "working_hours",
                "work_date",
                "date_source",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "pay_period_start": start_s,
                    "pay_period_end": end_s,
                    **row,
                }
            )

    by_day: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for row in rows:
        d = row["work_date"]
        total, count = by_day[d]
        by_day[d] = (total + row["working_hours"], count + 1)

    with daily_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["pay_period_start", "pay_period_end", "work_date", "working_hours", "task_count"],
        )
        writer.writeheader()
        grand_total = 0.0
        grand_count = 0
        for d in sorted(by_day):
            hrs, count = by_day[d]
            grand_total += hrs
            grand_count += count
            writer.writerow(
                {
                    "pay_period_start": start_s,
                    "pay_period_end": end_s,
                    "work_date": d,
                    "working_hours": round(hrs, 1) if hrs == int(hrs) else hrs,
                    "task_count": count,
                }
            )
        writer.writerow(
            {
                "pay_period_start": start_s,
                "pay_period_end": end_s,
                "work_date": "TOTAL",
                "working_hours": round(grand_total, 1) if grand_total == int(grand_total) else grand_total,
                "task_count": grand_count,
            }
        )

    return detail_path, daily_path


def print_summary(rows: list[dict]) -> None:
    bucket_totals: dict[str, float] = {b: 0.0 for b in STATUS_BUCKETS}
    bucket_counts: dict[str, int] = {b: 0 for b in STATUS_BUCKETS}
    other: list[dict] = []

    for row in rows:
        bucket = _bucket_status(row["status"])
        if bucket:
            bucket_totals[bucket] += row["working_hours"]
            bucket_counts[bucket] += 1
        else:
            other.append(row)

    total_hrs = sum(r["working_hours"] for r in rows)
    print(f"Tasks in range: {len(rows)}")
    print(f"Total hours: {total_hrs:.1f}")
    for bucket in STATUS_BUCKETS:
        print(f"  {bucket}: {bucket_totals[bucket]:.1f}h ({bucket_counts[bucket]} tasks)")
    if other:
        other_hrs = sum(r["working_hours"] for r in other)
        print(f"  Other statuses: {other_hrs:.1f}h ({len(other)} tasks)")
        for r in sorted(other, key=lambda x: -x["working_hours"]):
            print(f"    {r['working_hours']:5.1f}h | {r['status']} | {r['work_date']} | {r['task_name']}")


def main() -> None:
    board_name = sys.argv[1] if len(sys.argv) > 1 else "Je New To-Do List"
    slug = sys.argv[2] if len(sys.argv) > 2 else "je_hours"
    period_start = date(2026, 5, 31)
    period_end = date(2026, 6, 13)

    print(f"Board: {board_name}")
    print(f"Period: {period_start} to {period_end}")
    rows = load_board_hours_rows(
        board_name, period_start=period_start, period_end=period_end
    )
    print_summary(rows)
    detail_path, daily_path = write_csvs(
        slug, rows, period_start=period_start, period_end=period_end
    )
    print(f"\nWrote {detail_path}")
    print(f"Wrote {daily_path}")


if __name__ == "__main__":
    main()
