"""
Acquisition + retention dashboard on localhost.

    python acquisition_retention_report_http.py

Open:

    http://127.0.0.1:8849/

Period presets (query string):

    http://127.0.0.1:8849/?period=jan-apr-2026
    http://127.0.0.1:8849/?refresh=1
"""

from __future__ import annotations

import html
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from acquisition_retention_data import PERIOD_PRESETS, build_report, clear_caches

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

_cache: dict[str, dict] = {}
_cache_at: datetime | None = None
_loading: bool = False
_load_error: str | None = None

CSS = """
:root {
  font-family: system-ui, Segoe UI, Roboto, sans-serif;
  background: #0e1117; color: #e6edf3;
  --accent: #1f6feb; --green: #3fb950; --amber: #d29922; --red: #f85149;
  --panel: #161b22; --border: #30363d; --muted: #8b949e;
}
* { box-sizing: border-box; }
body { max-width: 1180px; margin: 0 auto; padding: 1.25rem 1rem 3rem; }
h1 { font-size: 1.55rem; font-weight: 600; margin: 0 0 0.35rem; }
.sub { color: var(--muted); font-size: 0.92rem; line-height: 1.5; margin-bottom: 1.25rem; }
.toolbar { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; margin-bottom: 1.25rem; }
.toolbar a {
  display: inline-block; padding: 0.4rem 0.75rem; border-radius: 6px;
  font-size: 0.85rem; text-decoration: none; border: 1px solid var(--border);
  color: #c9d1d9; background: var(--panel);
}
.toolbar a.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.toolbar a.btn { background: var(--accent); border-color: var(--accent); color: #fff; }
section { margin-bottom: 2rem; }
h2 { font-size: 1.15rem; margin: 0 0 0.75rem; padding-bottom: 0.35rem; border-bottom: 1px solid var(--border); }
h3 { font-size: 0.95rem; color: #c9d1d9; margin: 1.25rem 0 0.5rem; }
.panel {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.1rem; margin-bottom: 1rem;
}
.note { font-size: 0.85rem; color: var(--muted); line-height: 1.45; }
.note.warn { background: #2d1f0f; border: 1px solid #6e4c1a; color: #e3b341; padding: 0.85rem 1rem; border-radius: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; }
.card { background: #0d1117; border: 1px solid var(--border); border-radius: 8px; padding: 0.85rem 1rem; }
.card .k { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.card .v { font-size: 1.35rem; font-weight: 600; margin-top: 0.2rem; }
.card .v small { font-size: 0.8rem; font-weight: 400; color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
th, td { text-align: right; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); }
th:first-child, td:first-child { text-align: left; }
td.merged { vertical-align: middle; font-weight: 600; background: #0d1117; text-align: right; }
th { color: var(--muted); font-weight: 500; }
tr.total td { font-weight: 600; border-top: 2px solid var(--border); }
.pct-good { color: var(--green); }
.pct-warn { color: var(--amber); }
.pct-low { color: var(--red); }
.meta { font-size: 0.8rem; color: var(--muted); margin-top: 0.5rem; }
.err { background: #3d1111; border: 1px solid var(--red); color: #ffa198; padding: 1rem; border-radius: 8px; white-space: pre-wrap; }
.compare-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
.tag { display: inline-block; font-size: 0.72rem; padding: 0.15rem 0.45rem; border-radius: 4px; background: #21262d; color: var(--muted); margin-left: 0.35rem; }
"""


def _pct_class(v: float | None) -> str:
    if v is None:
        return ""
    if v >= 90:
        return "pct-good"
    if v >= 60:
        return "pct-warn"
    return "pct-low"


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def _fmt_int(v: int | None) -> str:
    if v is None:
        return "—"
    return f"{v:,}"


def _table(headers: list[str], rows: list[list[str]], total_row: list[str] | None = None) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cls = ' class="total"' if row and row[0] == "TOTAL" else ""
        tds = "".join(f"<td>{c}</td>" for c in row)
        body.append(f"<tr{cls}>{tds}</tr>")
    if total_row and (not rows or rows[-1][0] != "TOTAL"):
        tds = "".join(f"<td>{c}</td>" for c in total_row)
        body.append(f'<tr class="total">{tds}</tr>')
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _acquisition_funnel_table(acq: dict) -> str:
    """Acquisition table with Interest in FJ merged (rowspan) across all level rows + TOTAL."""
    level_rows = [
        r
        for r in acq["by_level"]
        if not (r["sign_ups"] == 0 and r["interest"] == 0 and r["level"] not in LEVELS_DISPLAY)
    ]
    row_count = len(level_rows) + 1  # membership rows + TOTAL
    interest_val = _fmt_int(acq["total_interest"])
    discover_val = _fmt_int(acq["total_discover"])

    headers = [
        "Level", "Interest in FJ", "Discover Calls", "Sign-ups",
        "Int→Disc", "Disc→Sign", "Int→Sign",
    ]
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body: list[str] = []

    for i, r in enumerate(level_rows):
        cells = [f"<td>{html.escape(r['level'])}</td>"]
        if i == 0:
            cells.append(
                f'<td rowspan="{row_count}" class="merged">{html.escape(interest_val)}</td>'
            )
            cells.append(f"<td>{html.escape(discover_val)}</td>")
            cells.append(f"<td>{_fmt_int(r['sign_ups'])}</td>")
            cells.append(f"<td>{_fmt_pct(r['int_to_disc_pct'])}</td>")
        else:
            cells.append(f"<td>{_fmt_int(r['sign_ups'])}</td>")
            cells.append("<td></td>")
        cells.append(f"<td>{_fmt_pct(r['disc_to_sign_pct'])}</td>")
        cells.append(f"<td>{_fmt_pct(r['int_to_sign_pct'])}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    t_int_disc = _fmt_pct(
        acq["total_discover"] / acq["total_interest"] * 100 if acq["total_interest"] else None
    )
    t_disc_sign = _fmt_pct(
        acq["total_sign_ups"] / acq["total_discover"] * 100 if acq["total_discover"] else None
    )
    t_int_sign = _fmt_pct(
        acq["total_sign_ups"] / acq["total_interest"] * 100 if acq["total_interest"] else None
    )
    body.append(
        f'<tr class="total"><td>TOTAL</td>'
        f"<td>{html.escape(discover_val)}</td>"
        f"<td>{_fmt_int(acq['total_sign_ups'])}</td>"
        f"<td>{t_int_disc}</td>"
        f"<td>{t_disc_sign}</td>"
        f"<td>{t_int_sign}</td></tr>"
    )

    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _ensure_period(period: str, refresh: bool = False) -> dict:
    global _cache_at, _loading, _load_error
    if refresh:
        clear_caches()
        _cache.clear()
    if period not in _cache:
        _loading = True
        _load_error = None
        try:
            _cache[period] = build_report(period)
            _cache_at = datetime.now(timezone.utc)
        except Exception as exc:
            _load_error = str(exc)
            raise
        finally:
            _loading = False
    return _cache[period]


def _ensure_all_presets(refresh: bool = False) -> dict[str, dict]:
    global _cache_at, _loading, _load_error
    from acquisition_retention_data import build_acquisition, build_retention, load_consolidated_by_name

    if refresh:
        clear_caches()
        _cache.clear()
    need = refresh or any(k not in _cache for k in PERIOD_PRESETS)
    if need:
        _loading = True
        _load_error = None
        try:
            consolidated = load_consolidated_by_name()
            for key, (label, start, end) in PERIOD_PRESETS.items():
                if refresh or key not in _cache:
                    _cache[key] = {
                        "preset": key,
                        "label": label,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "acquisition": build_acquisition(start, end),
                        "retention": build_retention(start, end, consolidated),
                    }
            _cache_at = datetime.now(timezone.utc)
        except Exception as exc:
            _load_error = str(exc)
            raise
        finally:
            _loading = False
    return _cache


def _loading_html() -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='15'>"
        f"<style>{CSS}</style></head><body>"
        "<h1>Loading dashboard…</h1>"
        "<p class='sub'>Fetching GoHighLevel + Google Sheets data. This can take 1–2 minutes on first load.</p>"
        "<p class='meta'>Page will refresh automatically. "
        "<a href='/ping'>Check server</a></p></body></html>"
    )


def _build_html(period: str, refresh: bool = False, error: str | None = None) -> str:
    if period not in PERIOD_PRESETS:
        period = "sept-apr-2026"

    try:
        data = _ensure_period(period, refresh=refresh)
        if refresh:
            all_data = _ensure_all_presets(refresh=True)
        else:
            all_data = _cache
        err_block = ""
    except Exception as exc:
        err_block = f'<div class="err">{html.escape(str(exc))}</div>'
        all_data = {}
        data = None

    nav = []
    for key, (label, _, _) in PERIOD_PRESETS.items():
        cls = "active" if key == period else ""
        nav.append(f'<a class="{cls}" href="/?period={html.escape(key)}">{html.escape(label)}</a>')
    nav.append(f'<a class="btn" href="/?period={html.escape(period)}&refresh=1">Refresh data</a>')

    ts = _cache_at.astimezone().strftime("%Y-%m-%d %H:%M %Z") if _cache_at else "—"

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Acquisition &amp; Retention Dashboard</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>Acquisition &amp; Retention Dashboard</h1>",
        "<p class='sub'>GoHighLevel acquisition funnel + Google Sheets retention (Consolidated Data). "
        "Five Journeys membership reporting — Sept 2025 through Apr 2026.</p>",
        f'<div class="toolbar">{"".join(nav)}</div>',
        err_block,
    ]

    if data:
        acq = data["acquisition"]
        ret = data["retention"]

        parts.append(f'<p class="meta">Showing <strong>{html.escape(data["label"])}</strong> '
                     f'({html.escape(data["start"])} to {html.escape(data["end"])}) · '
                     f'Generated {html.escape(ts)}</p>')

        # Summary cards
        parts.append("<section><h2>Summary</h2><div class='grid'>")
        cards = [
            ("Interest in FJ", _fmt_int(acq["total_interest"]), "New contacts (dateAdded)"),
            ("Discover Calls", _fmt_int(acq["total_discover"]), "Confirmed appointments"),
            ("Sign-ups", _fmt_int(acq["total_sign_ups"]), "GHL Sign Up Date"),
            ("Headline retention", _fmt_pct(ret["headline_retention_pct"]), "Name match (see caveats)"),
            ("Exact-match retention", _fmt_pct(ret["exact_match_retention_pct"]),
             f'{ret["total_exact_match"]} linked of {ret["total_sign_ups"]} sign-ups'),
            ("Terminations (all)", _fmt_int(ret["terminations_all"]), "Consolidated Data"),
        ]
        for k, v, sub in cards:
            parts.append(
                f'<div class="card"><div class="k">{html.escape(k)}</div>'
                f'<div class="v">{html.escape(v)}<br><small>{html.escape(sub)}</small></div></div>'
            )
        parts.append("</div></section>")

        # Acquisition funnel
        parts.append("<section><h2>Acquisition funnel by membership level</h2>")
        parts.append(
            '<p class="note">Interest in FJ and Discover Calls are <strong>period totals</strong> (same on every row). '
            "Sign-ups are per membership level.<br>"
            "<strong>Int→Disc</strong> = Total Discover Calls ÷ Total Interest in FJ<br>"
            "<strong>Disc→Sign</strong> = Sign-ups ÷ Total Discover Calls<br>"
            "<strong>Int→Sign</strong> = Sign-ups ÷ Total Interest in FJ</p>"
        )
        parts.append(_acquisition_funnel_table(acq))
        parts.append("</section>")

        # Monthly sign-ups
        if acq["monthly_signups"]:
            parts.append("<section><h2>Monthly sign-ups by level</h2>")
            mrows = []
            totals = Counter()
            for m in acq["monthly_signups"]:
                mrows.append([
                    m["month"],
                    _fmt_int(m["Standard"]),
                    _fmt_int(m["Gold"]),
                    _fmt_int(m["Silver"]),
                    _fmt_int(m["Platinum"]),
                    _fmt_int(m["n/a"]),
                    _fmt_int(m["total"]),
                ])
                for lv in ("Standard", "Gold", "Silver", "Platinum", "n/a"):
                    totals[lv] += m[lv]
                totals["total"] += m["total"]
            mrows.append([
                "TOTAL",
                _fmt_int(totals["Standard"]),
                _fmt_int(totals["Gold"]),
                _fmt_int(totals["Silver"]),
                _fmt_int(totals["Platinum"]),
                _fmt_int(totals["n/a"]),
                _fmt_int(totals["total"]),
            ])
            parts.append(_table(
                ["Month", "Standard", "Gold", "Silver", "Platinum", "n/a", "Total"],
                mrows,
            ))
            parts.append("</section>")

        # Retention
        parts.append("<section><h2>Retention by membership level</h2>")
        parts.append(
            '<div class="note warn"><strong>Methodology caveat:</strong> Retention links GHL sign-ups to '
            "Consolidated Data via <em>exact normalized name match</em> only (no nicknames, typos, or email). "
            "Unmatched sign-ups are counted as retained in the <strong>Headline</strong> column, which "
            "inflates that rate. Use <strong>Exact-match retention</strong> for a conservative estimate.</div>"
        )
        rrows = []
        for r in ret["by_level"]:
            if r["sign_ups"] == 0:
                continue
            hc = _pct_class(r["headline_retention_pct"])
            ec = _pct_class(r["exact_match_retention_pct"])
            rrows.append([
                r["level"],
                _fmt_int(r["sign_ups"]),
                _fmt_int(r["exact_match"]),
                _fmt_pct(r["match_rate_pct"]),
                _fmt_int(r["churned"]),
                f'<span class="{hc}">{_fmt_pct(r["headline_retention_pct"])}</span>',
                f'<span class="{ec}">{_fmt_pct(r["exact_match_retention_pct"])}</span>',
                _fmt_int(r["unmatched_assumed_retained"]),
            ])
        rrows.append([
            "TOTAL",
            _fmt_int(ret["total_sign_ups"]),
            _fmt_int(ret["total_exact_match"]),
            _fmt_pct(ret["match_rate_pct"]),
            _fmt_int(ret["total_churned"]),
            f'<span class="{_pct_class(ret["headline_retention_pct"])}">{_fmt_pct(ret["headline_retention_pct"])}</span>',
            f'<span class="{_pct_class(ret["exact_match_retention_pct"])}">{_fmt_pct(ret["exact_match_retention_pct"])}</span>',
            _fmt_int(ret["total_sign_ups"] - ret["total_exact_match"]),
        ])
        parts.append(_table(
            ["Level", "Sign-ups", "Exact match", "Match %", "Churned",
             "Headline retention", "Exact-match retention", "Unmatched"],
            rrows,
        ))
        parts.append("</section>")

        # Period comparison (available after Refresh loads all presets)
        if len(all_data) >= len(PERIOD_PRESETS):
            parts.append("<section><h2>Period comparison — retention trend</h2>")
            crows = []
            for key in PERIOD_PRESETS:
                p = all_data[key]
                pr = p["retention"]
                pa = p["acquisition"]
                crows.append([
                    p["label"],
                    _fmt_int(pa["total_sign_ups"]),
                    _fmt_int(pr["total_churned"]),
                    _fmt_pct(pr["headline_retention_pct"]),
                    _fmt_pct(pr["exact_match_retention_pct"]),
                    _fmt_pct(pr["match_rate_pct"]),
                ])
            parts.append(_table(
                ["Period", "Sign-ups", "Churned (matched)", "Headline retention",
                 "Exact-match retention", "Name match rate"],
                crows,
            ))
            parts.append("</section>")
        else:
            parts.append(
                '<section><h2>Period comparison</h2>'
                '<p class="note">Click <strong>Refresh data</strong> to load all three periods for side-by-side comparison '
                f"({len(all_data)}/{len(PERIOD_PRESETS)} cached).</p></section>"
            )

    parts.append(
        '<p class="meta">Sources: GoHighLevel (LeadConnector API) · '
        'Google Sheet <em>Consolidated Data</em> · '
        f'Run <code>python acquisition_retention_report_http.py</code> from project root.</p>'
    )
    parts.append("</body></html>")
    return "".join(parts)


LEVELS_DISPLAY = ("Standard", "Gold", "Silver", "Platinum", "n/a")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/ping", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        qs = parse_qs(parsed.query)
        period = (qs.get("period") or ["sept-apr-2026"])[0]
        refresh = "refresh" in qs

        if period not in PERIOD_PRESETS and parsed.path == "/":
            period = "sept-apr-2026"

        if parsed.path in ("/", "") and period not in _cache and _loading:
            body = _loading_html()
            code = 200
        else:
            try:
                body = _build_html(period=period, refresh=refresh)
                code = 200
            except Exception as exc:
                body = _build_html(period=period, refresh=False, error=str(exc))
                code = 500

        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _prewarm() -> None:
    try:
        print("Pre-loading default period (sept-apr-2026)…", flush=True)
        _ensure_period("sept-apr-2026")
        print("Default period ready.", flush=True)
    except Exception as exc:
        print(f"Pre-load failed: {exc}", flush=True)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8849
    host = "127.0.0.1"
    threading.Thread(target=_prewarm, daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Acquisition & Retention dashboard: http://{host}:{port}/", flush=True)
    print("First load may take 1–2 minutes while data is fetched.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
