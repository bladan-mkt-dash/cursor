"""
Serve the Google Ads + GHL conversion report as plain HTML (no Streamlit).

From the project directory:

    python google_ads_ghl_report_http.py

Then open (use **127.0.0.1**, not ``localhost``, if the browser fails — avoids IPv6)::

    http://127.0.0.1:8765/

Quick “is the server up?” check::

    http://127.0.0.1:8765/ping

Listen on all interfaces (firewall may prompt)::

    python google_ads_ghl_report_http.py --all

Optional query string (defaults Q1 2026)::

    http://127.0.0.1:8765/?since=2026-01-01&until=2026-03-31

Use a different port::

    python google_ads_ghl_report_http.py 8800
    python google_ads_ghl_report_http.py --all 8800
"""

from __future__ import annotations

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
from dotenv import load_dotenv

from ghl_client import (
    classify_hear_about_wom_vs_google,
    contact_custom_field_value,
    fetch_facebook_instagram_conversions,
    fetch_signup_date_range_committed_yes_contacts,
    resolve_hear_about_us_custom_field_id,
)

# Reuse Google Ads + tag helpers from the Streamlit module (import does not run Streamlit main).
from google_ads_ghl_conversion_report import (
    _contact_has_google_ads_path_tag,
    _fetch_google_ads_daily,
    _hear_about_bucket,
)

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

CSS = """
:root { font-family: system-ui, Segoe UI, Roboto, sans-serif; background: #0e1117; color: #fafafa; }
body { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 0.25rem; }
.sub { color: #a0a8b0; font-size: 0.9rem; margin-bottom: 1.5rem; }
section { margin-bottom: 2rem; }
h2 { font-size: 1.1rem; border-bottom: 1px solid #30363d; padding-bottom: 0.35rem; margin-top: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 0.85rem 1rem; }
.card .k { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.03em; }
.card .v { font-size: 1.25rem; font-weight: 600; margin-top: 0.25rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid #30363d; }
th { color: #8b949e; font-weight: 500; }
.err { background: #3d1111; border: 1px solid #f85149; color: #ffa198; padding: 1rem; border-radius: 8px; white-space: pre-wrap; }
a { color: #58a6ff; }
.bars { margin-top: 0.75rem; }
.bar-row { display: flex; align-items: center; gap: 0.5rem; margin: 0.35rem 0; font-size: 0.8rem; }
.bar-bg { flex: 1; height: 22px; background: #21262d; border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: #1f6feb; border-radius: 4px; }
"""


def _build_html(since: str, until: str) -> str:
    spend = 0.0
    parts: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'/>",
        f"<title>Google Ads + GHL — {html.escape(since)} to {html.escape(until)}</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>Google Ads + GoHighLevel</h1>",
        f"<p class='sub'>Range: <strong>{html.escape(since)}</strong> → <strong>{html.escape(until)}</strong> · "
        "Change dates: <code>?since=YYYY-MM-DD&amp;until=YYYY-MM-DD</code></p>",
    ]

    # --- Google Ads ---
    parts.append("<section><h2>Google Ads</h2>")
    try:
        ads_daily = _fetch_google_ads_daily(since, until)
        if ads_daily.empty:
            parts.append("<p>No campaign rows for this range.</p>")
            spend = imp = clicks = conv = 0.0
        else:
            spend = float(ads_daily["spend"].sum())
            imp = int(ads_daily["impressions"].sum())
            clicks = int(ads_daily["clicks"].sum())
            conv = float(ads_daily["conversions"].sum())
        parts.append('<div class="grid">')
        parts.append(
            f'<div class="card"><div class="k">Spend</div><div class="v">${spend:,.2f}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Impressions</div><div class="v">{imp:,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Clicks</div><div class="v">{clicks:,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Conversions</div><div class="v">{conv:,.1f}</div></div>'
        )
        parts.append("</div>")
        if not ads_daily.empty:
            ads_m = ads_daily.copy()
            ads_m["month"] = ads_m["date"].dt.to_period("M").astype(str)
            monthly = (
                ads_m.groupby("month", as_index=False)
                .agg({"spend": "sum"})
                .sort_values("month")
            )
            mx = float(monthly["spend"].max()) or 1.0
            parts.append('<div class="bars"><strong>Spend by month</strong>')
            for _, r in monthly.iterrows():
                pct = min(100.0, 100.0 * float(r["spend"]) / mx)
                parts.append(
                    f'<div class="bar-row"><span style="min-width:5.5rem">{html.escape(str(r["month"]))}</span>'
                    f'<div class="bar-bg"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
                    f'<span>${float(r["spend"]):,.0f}</span></div>'
                )
            parts.append("</div>")
    except Exception as e:
        parts.append(f'<div class="err">Google Ads API\n{html.escape(str(e))}</div>')
    parts.append("</section>")

    # --- GHL ---
    parts.append("<section><h2>GoHighLevel</h2>")
    try:
        hear_id = resolve_hear_about_us_custom_field_id(None)
        signup_committed = fetch_signup_date_range_committed_yes_contacts(
            since, until, location_id=None
        )
        contacts = signup_committed["contacts"]
        with_tag = [c for c in contacts if _contact_has_google_ads_path_tag(c)]
        hear_google = hear_blank = hear_other = 0
        for c in with_tag:
            raw = contact_custom_field_value(c, hear_id)
            b = _hear_about_bucket(raw)
            if b == "Google (field)":
                hear_google += 1
            elif b == "Blank / missing":
                hear_blank += 1
            else:
                hear_other += 1
        google_ch = hear_google + hear_blank
        parts.append('<div class="grid">')
        parts.append(
            f'<div class="card"><div class="k">Sign-up + Committed Yes</div><div class="v">{len(contacts):,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">+ Google Ads path tag</div><div class="v">{len(with_tag):,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Hear Google + blank</div><div class="v">{google_ch:,}</div></div>'
        )
        if google_ch and spend > 0:
            cpa = spend / google_ch
            parts.append(
                f'<div class="card"><div class="k">CPA (spend ÷ above)</div><div class="v">${cpa:,.2f}</div></div>'
            )
        parts.append("</div>")
        if signup_committed.get("truncated_pages"):
            parts.append("<p class='sub'>Warning: GHL sign-up search may be incomplete (pagination cap).</p>")
        parts.append("<h3 style='font-size:0.95rem;margin-top:1.25rem'>Hear about (tag cohort)</h3>")
        parts.append(
            "<table><thead><tr><th>Bucket</th><th>Count</th></tr></thead><tbody>"
            f"<tr><td>Google (field)</td><td>{hear_google:,}</td></tr>"
            f"<tr><td>Blank / missing</td><td>{hear_blank:,}</td></tr>"
            f"<tr><td>Other / WOM</td><td>{hear_other:,}</td></tr>"
            "</tbody></table>"
        )
    except Exception as e:
        parts.append(f'<div class="err">GHL\n{html.escape(str(e))}</div>')
    parts.append("</section>")

    # --- FB / IG ---
    parts.append("<section><h2>GHL — Facebook / Instagram (date added)</h2>")
    try:
        social = fetch_facebook_instagram_conversions(since, until, location_id=None)
        bs = social["by_source"]
        parts.append('<div class="grid">')
        parts.append(
            f'<div class="card"><div class="k">Facebook</div><div class="v">{bs.get("Facebook", 0):,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Instagram</div><div class="v">{bs.get("Instagram", 0):,}</div></div>'
        )
        parts.append(
            f'<div class="card"><div class="k">Total contacts</div><div class="v">{len(social["contacts"]):,}</div></div>'
        )
        parts.append("</div>")
        if social.get("truncated_pages"):
            parts.append("<p class='sub'>Warning: FB/IG search pagination cap.</p>")
    except Exception as e:
        parts.append(f'<div class="err">GHL FB/IG\n{html.escape(str(e))}</div>')
    parts.append("</section>")

    parts.append("</body></html>")
    return "".join(parts)


class ReuseAddressHTTPServer(HTTPServer):
    allow_reuse_address = True


class ReportHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/ping":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path != "/":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found. Use / or /ping\n")
            return

        qs = parse_qs(parsed.query)
        since = (qs.get("since") or ["2026-01-01"])[0].strip()
        until = (qs.get("until") or ["2026-03-31"])[0].strip()

        body = _build_html(since, until).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_args(argv: list[str]) -> tuple[str, int]:
    host = "127.0.0.1"
    port = 8765
    for a in argv[1:]:
        if a == "--all":
            host = "0.0.0.0"
        elif a.isdigit():
            port = int(a)
    if os.getenv("REPORT_HTTP_HOST", "").strip():
        host = os.getenv("REPORT_HTTP_HOST", "").strip()
    if os.getenv("REPORT_HTTP_PORT", "").strip().isdigit():
        port = int(os.getenv("REPORT_HTTP_PORT", "").strip())
    return host, port


def main() -> None:
    host, port = _parse_args(sys.argv)
    try:
        server = ReuseAddressHTTPServer((host, port), ReportHandler)
    except OSError as e:
        print(f"Could not bind to {host}:{port} — {e}", file=sys.stderr)
        print("Try another port, e.g.:  python google_ads_ghl_report_http.py 8899", file=sys.stderr)
        sys.exit(1)

    primary = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{primary}:{port}/"
    ping = f"http://{primary}:{port}/ping"
    print(f"Serving report at {url}")
    print(f"Health check:  {ping}")
    if host == "0.0.0.0":
        print("Bound on 0.0.0.0 — also try this machine's LAN IP if needed.")
    print("")
    print("If the browser says connection failed:")
    print("  1. Keep this terminal open — closing it stops the server.")
    print("  2. Use the URL above with 127.0.0.1 (not localhost) first.")
    print("  3. Open /ping — should show the word ok; if not, nothing is listening.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
