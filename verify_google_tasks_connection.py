"""Quick check that Google Tasks API access works."""

from __future__ import annotations

from google_tasks_client import fetch_task_alerts, list_tasklists, tasks_service


def main() -> int:
    print("Google Tasks connection check")
    try:
        service = tasks_service()
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"NOT READY: {exc}")
        return 1

    lists = list_tasklists(service)
    print(f"OK: authenticated — {len(lists)} task list(s)")
    for row in lists[:8]:
        print(f"    - {(row.get('title') or '?')} ({row.get('id')})")
    if len(lists) > 8:
        print(f"    … and {len(lists) - 8} more")

    alerts = fetch_task_alerts(service)
    overdue = sum(1 for a in alerts if a.severity.value == "Overdue")
    today = sum(1 for a in alerts if a.severity.value == "Due today")
    soon = sum(1 for a in alerts if a.severity.value == "Due soon")
    print(f"Alerts (with due dates): {len(alerts)} — overdue {overdue}, due today {today}, due soon {soon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
