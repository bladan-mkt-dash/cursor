"""
Serve the TOTAL New Members YoY chart on localhost.

    python total_new_members_report_http.py

Open:

    http://127.0.0.1:8848/

Refresh live data from Google Sheets:

    http://127.0.0.1:8848/?refresh=1
"""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

import pandas as pd

from total_new_members_yoy_chart import OUTPUT_DIR, build_report

_PROJECT_DIR = Path(__file__).resolve().parent
CHART_PNG = OUTPUT_DIR / "total_new_members_mom_2023_2024_2025.png"
CHART_CSV = OUTPUT_DIR / "total_new_members_mom_2023_2024_2025.csv"

_cache: dict[str, object] = {}


def _ensure_report(refresh: bool = False) -> tuple[pd.DataFrame, list[str], datetime]:
    if (
        not refresh
        and "df" in _cache
        and CHART_PNG.exists()
        and CHART_CSV.exists()
    ):
        return _cache["df"], _cache["sources"], _cache["generated_at"]  # type: ignore[return-value]

    if not refresh and CHART_CSV.exists() and CHART_PNG.exists() and "df" not in _cache:
        df = pd.read_csv(CHART_CSV, index_col=0)
        _cache["df"] = df
        _cache["sources"] = [
            "2023: 2023 Digital Cross-Channel Tracker / Monthly Tracker",
            "2024: 2024 Digital Cross-Channel Tracker / Monthly Tracker",
            "2025: 2025 Digital Cross-Channel Tracker / Monthly Tracker",
        ]
        _cache["generated_at"] = datetime.fromtimestamp(
            CHART_PNG.stat().st_mtime, tz=timezone.utc
        )
        return _cache["df"], _cache["sources"], _cache["generated_at"]  # type: ignore[return-value]

    df, sources, png_path, _csv_path = build_report()
    _cache["df"] = df
    _cache["sources"] = sources
    _cache["generated_at"] = datetime.now(timezone.utc)
    return _cache["df"], _cache["sources"], _cache["generated_at"]  # type: ignore[return-value]


CSS = """
:root { font-family: system-ui, Segoe UI, Roboto, sans-serif; background: #0e1117; color: #fafafa; }
body { max-width: 1200px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
h1 { font-size: 1.55rem; font-weight: 600; margin-bottom: 0.25rem; }
.sub { color: #a0a8b0; font-size: 0.92rem; margin-bottom: 1.25rem; line-height: 1.45; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 1rem 1.1rem; margin-bottom: 1.25rem; }
.chart-wrap { overflow-x: auto; }
.chart-wrap img { max-width: 100%; height: auto; border-radius: 8px; background: #fff; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: right; padding: 0.55rem 0.65rem; border-bottom: 1px solid #30363d; }
th:first-child, td:first-child { text-align: left; }
th { color: #8b949e; font-weight: 500; }
.sources { font-size: 0.82rem; color: #8b949e; margin: 0; padding-left: 1.1rem; }
.sources li { margin: 0.25rem 0; }
.toolbar { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1rem; }
a.btn { display: inline-block; background: #1f6feb; color: #fff; text-decoration: none; padding: 0.45rem 0.85rem; border-radius: 6px; font-size: 0.85rem; }
a.btn.secondary { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; }
.meta { color: #8b949e; font-size: 0.82rem; }
"""


def _build_html(refresh: bool = False) -> str:
    df, sources, generated_at = _ensure_report(refresh=refresh)
    ts = generated_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    rows = []
    for month, row in df.iterrows():
        cells = [f"<td>{html.escape(str(month))}</td>"]
        for year in ("2023", "2024", "2025"):
            val = row[year]
            text = "" if pd.isna(val) else str(int(val))
            cells.append(f"<td>{html.escape(text)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    source_items = "".join(f"<li>{html.escape(s)}</li>" for s in sources)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TOTAL New Members — 2023 vs 2024 vs 2025</title>
  <style>{CSS}</style>
</head>
<body>
  <h1>TOTAL New Members</h1>
  <p class="sub">Month-over-month comparison across the 2023, 2024, and 2025 Digital Cross-Channel trackers
  (Monthly Tracker sheet).</p>

  <div class="toolbar">
    <a class="btn" href="/?refresh=1">Refresh from Google Sheets</a>
    <a class="btn secondary" href="/chart.png">Download chart PNG</a>
    <a class="btn secondary" href="/data.csv">Download CSV</a>
    <span class="meta">Last updated: {html.escape(ts)}</span>
  </div>

  <div class="panel chart-wrap">
    <img src="/chart.png?t={html.escape(str(int(generated_at.timestamp())))}" alt="TOTAL New Members grouped bar chart">
  </div>

  <div class="panel">
    <table>
      <thead>
        <tr><th>Month</th><th>2023</th><th>2024</th><th>2025</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </div>

  <div class="panel">
    <p class="meta" style="margin-top:0;">Data sources</p>
    <ul class="sources">{source_items}</ul>
  </div>
</body>
</html>"""


class ReuseAddressHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class ReportHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")

        if path == "/ping":
            self._send_bytes(200, "text/plain; charset=utf-8", b"ok\n")
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path == "/chart.png":
            _ensure_report(refresh=refresh)
            if not CHART_PNG.exists():
                self.send_response(404)
                self.end_headers()
                return
            body = CHART_PNG.read_bytes()
            self._send_bytes(200, "image/png", body)
            return

        if path == "/data.csv":
            _ensure_report(refresh=refresh)
            if not CHART_CSV.exists():
                self.send_response(404)
                self.end_headers()
                return
            body = CHART_CSV.read_bytes()
            self._send_bytes(200, "text/csv; charset=utf-8", body)
            return

        if path != "/":
            self._send_bytes(404, "text/plain; charset=utf-8", b"Not found. Use / or /ping\n")
            return

        body = _build_html(refresh=refresh).encode("utf-8")
        self._send_bytes(200, "text/html; charset=utf-8", body)


def _parse_args(argv: list[str]) -> tuple[str, int]:
    host = "127.0.0.1"
    port = 8848
    for arg in argv[1:]:
        if arg == "--all":
            host = "0.0.0.0"
        elif arg.isdigit():
            port = int(arg)
    if os.getenv("REPORT_HTTP_HOST", "").strip():
        host = os.getenv("REPORT_HTTP_HOST", "").strip()
    if os.getenv("REPORT_HTTP_PORT", "").strip().isdigit():
        port = int(os.getenv("REPORT_HTTP_PORT", "").strip())
    return host, port


def main() -> None:
    host, port = _parse_args(sys.argv)
    if CHART_PNG.exists() and CHART_CSV.exists():
        print("Using cached chart (add ?refresh=1 in browser to pull live Google Sheets data).")
    else:
        print("No cached chart found — fetching from Google Sheets on first request...")

    try:
        server = ReuseAddressHTTPServer((host, port), ReportHandler)
    except OSError as exc:
        print(f"Could not bind to {host}:{port} — {exc}", file=sys.stderr)
        print("Try another port:  python total_new_members_report_http.py 8898", file=sys.stderr)
        sys.exit(1)

    primary = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{primary}:{port}/"
    print(f"Serving report at {url}")
    print(f"Health check:  http://{primary}:{port}/ping")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
