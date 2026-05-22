"""
Monday.com team activity dashboard — to-do lists across boards with project filters.

Setup:
  1. monday.com -> Profile -> Developers -> My Access Tokens -> Generate
  2. Add to project .env:  MONDAY_API_TOKEN=your_token_here
  3. Verify:  python verify_monday_connection.py
  4. Run:  streamlit run monday_team_activity.py
  5. Open:  http://127.0.0.1:8501/

Board IDs appear in verify_monday_connection.py output; select boards in the sidebar.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from monday_client import (
    fetch_items_from_boards,
    list_boards,
    list_workspaces,
    summarize_items,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

COLORS = {
    "accent": "#5DA68A",
    "accent_dark": "#264540",
    "overdue": "#E45756",
    "muted": "#6B7C93",
    "warning": "#F58518",
}


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; max-width: 1400px; }
        [data-testid="stMetric"] {
            background: white;
            border-radius: 12px;
            padding: 0.75rem 1rem;
            box-shadow: 0 1px 3px rgba(38,69,64,0.08);
            border: 1px solid rgba(93,166,138,0.15);
        }
        [data-testid="stMetricLabel"] { color: #264540; font-weight: 600; }
        [data-testid="stMetricValue"] { color: #5DA68A; }
        h5 { color: #264540; margin-top: 1rem !important; }
        [data-testid="stSidebar"] { background: #f7fbff; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_row(items: list[tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)


@st.cache_data(ttl=600, show_spinner=False)
def _load_board_catalog() -> tuple[list[dict], list[dict]]:
    workspaces = list_workspaces()
    boards = list_boards(limit=100)
    return workspaces, boards


@st.cache_data(ttl=300, show_spinner=False)
def _load_tasks(board_ids: tuple[str, ...]) -> pd.DataFrame:
    if not board_ids:
        return pd.DataFrame()
    _, boards = _load_board_catalog()
    name_map = {str(b["id"]): str(b.get("name") or "") for b in boards if b.get("id")}
    df, _ = fetch_items_from_boards(list(board_ids), board_names=name_map)
    return df


def _status_bar_chart(by_status: dict[str, int]):
    if not by_status:
        return None
    plot_df = pd.DataFrame(
        [{"status": k, "count": v} for k, v in sorted(by_status.items(), key=lambda x: -x[1])]
    )
    fig = px.bar(
        plot_df,
        x="status",
        y="count",
        title="Tasks by status",
        color_discrete_sequence=[COLORS["accent"]],
    )
    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="",
        yaxis_title="Count",
        font=dict(color=COLORS["accent_dark"]),
    )
    return fig


def _assignee_bar_chart(by_assignee: dict[str, int], *, top_n: int = 12):
    if not by_assignee:
        return None
    rows = sorted(by_assignee.items(), key=lambda x: -x[1])[:top_n]
    plot_df = pd.DataFrame([{"assignee": k, "count": v} for k, v in rows])
    fig = px.bar(
        plot_df,
        x="count",
        y="assignee",
        orientation="h",
        title=f"Tasks by assignee (top {min(top_n, len(rows))})",
        color_discrete_sequence=[COLORS["accent_dark"]],
    )
    fig.update_layout(
        height=max(280, 40 * len(rows)),
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis={"categoryorder": "total ascending"},
        font=dict(color=COLORS["accent_dark"]),
    )
    return fig


def main() -> None:
    st.set_page_config(page_title="Monday Team Activity", layout="wide", page_icon="✅")
    _inject_styles()

    st.title("Monday Team Activity")
    st.caption("Track team to-do items across Monday.com boards · filter by project, assignee, and status")

    try:
        with st.spinner("Loading Monday.com boards…"):
            workspaces, boards = _load_board_catalog()
    except ValueError as exc:
        st.error(str(exc))
        st.info(
            "monday.com -> Profile -> Developers -> My Access Tokens -> Generate, "
            "then add `MONDAY_API_TOKEN=...` to `.env` and run `python verify_monday_connection.py`."
        )
        st.stop()
    except RuntimeError as exc:
        st.error(f"Monday.com API error: {exc}")
        st.stop()

    if not boards:
        st.warning("No boards found for this token.")
        st.stop()

    active_boards = [b for b in boards if (b.get("state") or "").casefold() == "active"]
    board_options = active_boards or boards
    board_labels = {
        str(b["id"]): f"{b.get('name')} ({b.get('id')})" for b in board_options if b.get("id")
    }
    board_ids_sorted = sorted(board_labels.keys(), key=lambda i: board_labels[i].casefold())

    with st.sidebar:
        st.header("Filters")

        if workspaces:
            ws_names = {str(w["id"]): w.get("name") or f"Workspace {w['id']}" for w in workspaces}
            selected_ws = st.multiselect(
                "Workspace",
                options=sorted(ws_names.keys(), key=lambda i: ws_names[i].casefold()),
                format_func=lambda i: ws_names[i],
                placeholder="All workspaces",
            )
        else:
            selected_ws = []

        if selected_ws:
            filtered_board_ids = [
                bid
                for bid in board_ids_sorted
                if str(next((b for b in board_options if str(b.get("id")) == bid), {}).get("workspace_id"))
                in selected_ws
            ]
        else:
            filtered_board_ids = board_ids_sorted

        selected_boards = st.multiselect(
            "Boards / projects",
            options=filtered_board_ids,
            default=filtered_board_ids[: min(5, len(filtered_board_ids))],
            format_func=lambda i: board_labels[i],
        )

        show_overdue_only = st.checkbox("Overdue only", value=False)
        hide_done = st.checkbox("Hide completed / done", value=True)

        if st.button("Refresh data"):
            _load_board_catalog.clear()
            _load_tasks.clear()
            st.rerun()

    if not selected_boards:
        st.warning("Select at least one board in the sidebar.")
        st.stop()

    with st.spinner("Loading tasks from selected boards…"):
        try:
            raw_df = _load_tasks(tuple(selected_boards))
        except RuntimeError as exc:
            st.error(f"Could not load tasks: {exc}")
            st.stop()

    if raw_df.empty:
        st.info("No items found on the selected boards.")
        st.stop()

    df = raw_df.copy()

    assignees = sorted(df["assignee"].dropna().unique())
    statuses = sorted(df["status"].dropna().unique())

    with st.sidebar:
        selected_assignees = st.multiselect(
            "Assignee",
            assignees,
            default=assignees,
            placeholder="All assignees",
        )
        selected_statuses = st.multiselect(
            "Status",
            statuses,
            default=statuses,
            placeholder="All statuses",
        )

    if selected_assignees:
        df = df[df["assignee"].isin(selected_assignees)]
    if selected_statuses:
        df = df[df["status"].isin(selected_statuses)]
    if hide_done:
        done_labels = {
            s for s in df["status"].unique() if str(s).casefold() in {
                "done", "complete", "completed", "finished", "closed",
                "won't do", "wont do", "cancelled", "canceled",
            }
        }
        if done_labels:
            df = df[~df["status"].isin(done_labels)]
    if show_overdue_only:
        df = df[df["overdue"] == True]  # noqa: E712

    summary = summarize_items(df)

    st.markdown("##### Summary")
    _metric_row(
        [
            ("Total tasks", f"{summary['total']:,}"),
            ("Overdue", f"{summary['overdue_count']:,}"),
            ("Unassigned", f"{summary['unassigned_count']:,}"),
            ("Boards", f"{df['board_name'].nunique():,}"),
        ]
    )

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        status_fig = _status_bar_chart(summary["by_status"])
        if status_fig:
            st.plotly_chart(status_fig, use_container_width=True)
    with c2:
        assignee_fig = _assignee_bar_chart(summary["by_assignee"])
        if assignee_fig:
            st.plotly_chart(assignee_fig, use_container_width=True)

    if summary["overdue_count"] > 0 and not show_overdue_only:
        st.markdown("##### Overdue items")
        overdue_df = df[df["overdue"] == True][  # noqa: E712
            ["name", "assignee", "status", "due_date", "board_name"]
        ]
        st.dataframe(overdue_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("All tasks")

    display_cols = ["name", "assignee", "status", "due_date", "board_name", "updated_at"]
    table_df = df[display_cols].rename(
        columns={
            "name": "Task",
            "assignee": "Assignee",
            "status": "Status",
            "due_date": "Due date",
            "board_name": "Board",
            "updated_at": "Updated",
        }
    )
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    with st.expander("Column mapping (auto-detected per board)"):
        mapping_cols = ["board_name", "status_column", "assignee_column", "due_date_column"]
        if all(c in df.columns for c in mapping_cols):
            st.dataframe(
                df[mapping_cols].drop_duplicates().sort_values("board_name"),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No column mapping metadata available.")


if __name__ == "__main__":
    main()
