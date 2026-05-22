"""CLI: top organic FB+IG posts matching women's-health text; rank by interactions, then impressions."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_PROJECT = Path(__file__).resolve().parent
_OUTPUT_CSV = _PROJECT / "meta_top_womens_health_organic_posts.csv"
load_dotenv(_PROJECT / ".env")

GRAPH = "v21.0"
BASE = f"https://graph.facebook.com/{GRAPH}"

WOMENS_HEALTH_TOKENS = (
    "women's health",
    "womens health",
    "women health",
    "female health",
    "gynecolog",
    "obstetric",
    "obgyn",
    "o.b.g.y.n",
    "prenatal",
    "postpartum",
    "pregnancy",
    "pregnant",
    "menopause",
    "mammogram",
    "cervical",
    "pap smear",
    "pap test",
    "breast health",
    "pcos",
    "endometriosis",
    "fertility",
    "maternal",
    "reproductive health",
    "pelvic",
    "uterine",
    "ovarian",
    "hormonal health",
    "birth control",
    "contraception",
    "breastfeeding",
    "lactation",
    "miscarriage",
    "well-woman",
    "well woman",
)


def _token() -> str:
    return (
        (
            os.getenv("META_SYSTEM_USER_TOKEN")
            or os.getenv("META_USER_ACCESS_TOKEN")
            or os.getenv("META_ACCESS_TOKEN")
            or os.getenv("FB_ACCESS_TOKEN")
            or ""
        )
        .strip()
    )


def _page_id_pref() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID") or "").strip()


def _ad_account_id() -> str:
    raw = (os.getenv("META_AD_ACCOUNT_ID") or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("act_") else f"act_{raw}"


def _get(path: str, params: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    url = f"{BASE}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}\n{r.text}")
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"]))
    return data


def _page_access_token(user_token: str, page_id: str | None) -> tuple[str, str]:
    payload = _get(
        "me/accounts",
        {"fields": "id,name,access_token", "limit": 200, "access_token": user_token},
    )
    pages: list[dict[str, Any]] = payload.get("data") or []
    if not pages:
        raise RuntimeError("No Facebook Pages returned for this token.")
    if page_id:
        for p in pages:
            if str(p.get("id")) == page_id:
                t = (p.get("access_token") or "").strip()
                if t:
                    return page_id, t
        raise RuntimeError(f"FACEBOOK_PAGE_ID={page_id} not found in /me/accounts.")
    first = pages[0]
    return str(first["id"]).strip(), str(first.get("access_token") or "").strip()


def _page_token_for_posts(page_id: str, user_token: str) -> str:
    payload = _get(
        "me/accounts",
        {"fields": "id,access_token", "limit": 200, "access_token": user_token},
    )
    for p in payload.get("data") or []:
        if str(p.get("id") or "").strip() == page_id:
            t = (p.get("access_token") or "").strip()
            if t:
                return t
    raise RuntimeError("Could not resolve Page access token for post reads.")


def _ig_user_id(page_id: str, page_token: str) -> str:
    data = _get(page_id, {"fields": "instagram_business_account", "access_token": page_token})
    ig = data.get("instagram_business_account") or {}
    ig_id = str(ig.get("id") or "").strip()
    if ig_id:
        return ig_id
    fb = (os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID") or "").strip()
    if fb:
        return fb
    raise RuntimeError("No linked Instagram business account.")


def _womens_health_match(text: str) -> bool:
    if not text or not text.strip():
        return False
    low = text.casefold()
    for tok in WOMENS_HEALTH_TOKENS:
        t = tok.casefold().strip()
        if " " in t or "/" in t:
            if t in low:
                return True
        else:
            if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low):
                return True
    return False


def _post_title(text: str, *, max_len: int = 200) -> str:
    """Short headline from caption/message (first line, before hashtags)."""
    if not text or not text.strip():
        return ""
    line = text.strip().split("\n")[0].strip()
    one_line = " ".join(text.strip().split())
    if len(line) < 40:
        for sep in (". ", "! ", "? "):
            idx = one_line.find(sep)
            if 0 <= idx < max_len * 2:
                candidate = one_line[: idx + 1].strip()
                if len(candidate) > len(line):
                    line = candidate
                break
    hash_idx = line.find("#")
    if hash_idx > 0:
        before = line[:hash_idx].strip()
        if len(before) >= 15:
            line = before
    if len(line) > max_len:
        line = line[: max_len - 1].rstrip() + "…"
    return line


def _fb_parse_impressions(insights_obj: Any) -> int | None:
    if not isinstance(insights_obj, dict):
        return None
    for row in insights_obj.get("data") or []:
        if row.get("name") != "post_impressions":
            continue
        vals = row.get("values") or []
        if not vals:
            return None
        v = vals[0].get("value")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _fetch_fb_posts(page_id: str, page_token: str, *, max_posts: int) -> list[dict[str, Any]]:
    fields = (
        "id,created_time,message,permalink_url,shares,"
        "reactions.limit(0).summary(true),comments.limit(0).summary(true),"
        "insights.metric(post_impressions).period(lifetime)"
    )
    url = f"{BASE}/{page_id}/posts"
    params: dict[str, Any] = {"fields": fields, "limit": 100, "access_token": page_token}
    out: list[dict[str, Any]] = []
    while url and len(out) < max_posts:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok:
            err_txt = (r.text or "").lower()
            if len(out) == 0 and ("insights" in err_txt or "metric" in err_txt):
                return _fetch_fb_posts_plain(page_id, page_token, max_posts=max_posts)
            raise RuntimeError(f"FB posts HTTP {r.status_code}\n{r.text}")
        if data.get("error"):
            err = data["error"]
            if len(out) == 0 and isinstance(err, dict):
                msg = (err.get("message") or "").lower()
                if "insights" in msg or err.get("code") == 100:
                    return _fetch_fb_posts_plain(page_id, page_token, max_posts=max_posts)
            raise RuntimeError(json.dumps(err))
        for post in data.get("data") or []:
            reactions = ((post.get("reactions") or {}).get("summary") or {}).get("total_count", 0)
            comments = ((post.get("comments") or {}).get("summary") or {}).get("total_count", 0)
            shares = (post.get("shares") or {}).get("count", 0)
            interactions = int(reactions or 0) + int(comments or 0) + int(shares or 0)
            impressions = _fb_parse_impressions(post.get("insights"))
            msg = (post.get("message") or "").strip()
            link = (post.get("permalink_url") or "").strip()
            out.append(
                {
                    "platform": "Facebook",
                    "post_id": str(post.get("id") or ""),
                    "created_time": str(post.get("created_time") or ""),
                    "title": _post_title(msg),
                    "text": msg,
                    "link": link,
                    "permalink": link,
                    "interactions": interactions,
                    "impressions": impressions if impressions is not None else -1,
                }
            )
            if len(out) >= max_posts:
                break
        next_url = (data.get("paging") or {}).get("next")
        url = str(next_url) if next_url else ""
        params = {}
    return out


def _fetch_fb_posts_plain(page_id: str, page_token: str, *, max_posts: int) -> list[dict[str, Any]]:
    fields = (
        "id,created_time,message,permalink_url,shares,"
        "reactions.limit(0).summary(true),comments.limit(0).summary(true)"
    )
    url = f"{BASE}/{page_id}/posts"
    params: dict[str, Any] = {"fields": fields, "limit": 100, "access_token": page_token}
    out: list[dict[str, Any]] = []
    while url and len(out) < max_posts:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok:
            raise RuntimeError(f"FB posts HTTP {r.status_code}\n{r.text}")
        if data.get("error"):
            raise RuntimeError(json.dumps(data["error"]))
        for post in data.get("data") or []:
            reactions = ((post.get("reactions") or {}).get("summary") or {}).get("total_count", 0)
            comments = ((post.get("comments") or {}).get("summary") or {}).get("total_count", 0)
            shares = (post.get("shares") or {}).get("count", 0)
            interactions = int(reactions or 0) + int(comments or 0) + int(shares or 0)
            msg = (post.get("message") or "").strip()
            pid = str(post.get("id") or "")
            link = (post.get("permalink_url") or "").strip()
            out.append(
                {
                    "platform": "Facebook",
                    "post_id": pid,
                    "created_time": str(post.get("created_time") or ""),
                    "title": _post_title(msg),
                    "text": msg,
                    "link": link,
                    "permalink": link,
                    "interactions": interactions,
                    "impressions": -1,
                }
            )
            if len(out) >= max_posts:
                break
        next_url = (data.get("paging") or {}).get("next")
        url = str(next_url) if next_url else ""
        params = {}
    return out


def _ig_impressions(media_id: str, token: str) -> int | None:
    try:
        ins = _get(
            f"{media_id}/insights",
            {"metric": "impressions", "access_token": token},
            timeout=30,
        )
    except RuntimeError:
        return None
    for row in ins.get("data") or []:
        if row.get("name") == "impressions":
            vals = row.get("values") or []
            if vals:
                try:
                    return int(vals[0].get("value") or 0)
                except (TypeError, ValueError):
                    return None
    return None


def _fb_post_impressions(post_id: str, page_token: str) -> int | None:
    if not post_id:
        return None
    try:
        ins = _get(
            f"{post_id}/insights",
            {
                "metric": "post_impressions",
                "period": "lifetime",
                "access_token": page_token,
            },
            timeout=30,
        )
    except RuntimeError:
        return None
    for row in ins.get("data") or []:
        if row.get("name") == "post_impressions":
            vals = row.get("values") or []
            if vals:
                try:
                    return int(vals[0].get("value") or 0)
                except (TypeError, ValueError):
                    return None
    return None


def _enrich_impressions(rows: list[dict[str, Any]], page_token: str) -> None:
    """Fill impressions for a small filtered list (avoids hundreds of Graph calls)."""
    for r in rows:
        if r["platform"] == "Facebook":
            imp = _fb_post_impressions(r["post_id"], page_token)
        else:
            imp = _ig_impressions(r["post_id"], page_token)
        r["impressions"] = imp if imp is not None else -1


def _fetch_ig_media(ig_user_id: str, page_token: str, *, max_posts: int) -> list[dict[str, Any]]:
    fields = "id,caption,timestamp,permalink,like_count,comments_count"
    url = f"{BASE}/{ig_user_id}/media"
    params: dict[str, Any] = {"fields": fields, "limit": 100, "access_token": page_token}
    out: list[dict[str, Any]] = []
    while url and len(out) < max_posts:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok:
            raise RuntimeError(f"IG media HTTP {r.status_code}\n{r.text}")
        if data.get("error"):
            raise RuntimeError(json.dumps(data["error"]))
        for post in data.get("data") or []:
            likes = int(post.get("like_count") or 0)
            comments = int(post.get("comments_count") or 0)
            cap = (post.get("caption") or "").strip()
            mid = str(post.get("id") or "")
            link = (post.get("permalink") or "").strip()
            out.append(
                {
                    "platform": "Instagram",
                    "post_id": mid,
                    "created_time": str(post.get("timestamp") or ""),
                    "title": _post_title(cap),
                    "text": cap,
                    "link": link,
                    "permalink": link,
                    "interactions": likes + comments,
                    "impressions": -1,
                }
            )
            if len(out) >= max_posts:
                break
        next_url = (data.get("paging") or {}).get("next")
        url = str(next_url) if next_url else ""
        params = {}
    return out


def _paid_markers(user_token: str) -> tuple[set[str], set[str]]:
    fb_ids: set[str] = set()
    ig_ids: set[str] = set()
    acct = _ad_account_id()
    if not acct:
        return fb_ids, ig_ids
    url = f"{BASE}/{acct}/adcreatives"
    params: dict[str, Any] = {
        "fields": "object_story_id,effective_object_story_id,source_instagram_media_id",
        "limit": 200,
        "access_token": user_token,
    }
    pages = 0
    while url and pages < 80:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if not r.ok or data.get("error"):
            break
        for c in data.get("data") or []:
            for key in ("object_story_id", "effective_object_story_id"):
                sid = str(c.get(key) or "").strip()
                if sid and "_" in sid:
                    fb_ids.add(sid)
                    fb_ids.add(sid.split("_", 1)[-1])
            ig_mid = str(c.get("source_instagram_media_id") or "").strip()
            if ig_mid:
                ig_ids.add(ig_mid)
        next_url = (data.get("paging") or {}).get("next")
        url = str(next_url) if next_url else ""
        params = {}
        pages += 1
    return fb_ids, ig_ids


def _is_paid_fb(post_id: str, paid: set[str]) -> bool:
    if not post_id:
        return False
    if post_id in paid:
        return True
    return post_id.split("_")[-1] in paid


def _is_paid_ig(post_id: str, paid: set[str]) -> bool:
    return bool(post_id and post_id in paid)


def fetch_womens_health_organic_ranked(
    *,
    top_n: int = 5,
    max_scan: int = 400,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Organic FB + IG posts whose text matches women's-health keywords, excluding paid
    markers from ``META_AD_ACCOUNT_ID``. Sorted by interactions, then impressions.

    Returns:
        ``(top_rows, info)`` where each row has keys:
        ``platform``, ``post_id``, ``created_time``, ``title``, ``text``, ``link``,
        ``permalink``, ``interactions``, ``impressions`` (``-1`` = unknown).
        ``info`` has ``page_id``, ``ig_user_id``, ``matched_count``, ``errors`` (list).
    """
    errors: list[str] = []
    user_tok = _token()
    if not user_tok:
        return [], {"errors": ["Set META_SYSTEM_USER_TOKEN or META_USER_ACCESS_TOKEN in .env."]}

    page_pref = _page_id_pref() or None
    try:
        page_id, page_token = _page_access_token(user_tok, page_pref)
        posts_token = _page_token_for_posts(page_id, user_tok)
        ig_id = _ig_user_id(page_id, page_token)
    except RuntimeError as e:
        return [], {"errors": [str(e)]}

    fb_paid, ig_paid = _paid_markers(user_tok)
    rows: list[dict[str, Any]] = []
    try:
        fb_rows = _fetch_fb_posts(page_id, posts_token, max_posts=max_scan)
    except RuntimeError as e:
        errors.append(f"Facebook: {e}")
        fb_rows = []
    try:
        ig_rows = _fetch_ig_media(ig_id, page_token, max_posts=max_scan)
    except RuntimeError as e:
        errors.append(f"Instagram: {e}")
        ig_rows = []

    for r in fb_rows:
        if _is_paid_fb(r["post_id"], fb_paid):
            continue
        if _womens_health_match(r["text"]):
            rows.append(r)
    for r in ig_rows:
        if _is_paid_ig(r["post_id"], ig_paid):
            continue
        if _womens_health_match(r["text"]):
            rows.append(r)

    try:
        _enrich_impressions(rows, posts_token)
    except RuntimeError:
        pass

    rows.sort(
        key=lambda x: (x["interactions"], x["impressions"] if x["impressions"] >= 0 else -1),
        reverse=True,
    )
    top = rows[: int(top_n)]

    info: dict[str, Any] = {
        "page_id": page_id,
        "ig_user_id": ig_id,
        "matched_count": len(rows),
        "errors": errors,
    }
    return top, info


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "rank",
        "platform",
        "title",
        "link",
        "interactions",
        "impressions",
        "created_time",
        "post_id",
        "text",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for i, row in enumerate(rows, 1):
            imp = row.get("impressions", -1)
            link = (row.get("link") or row.get("permalink") or "").strip()
            title = (row.get("title") or _post_title(str(row.get("text") or ""))).strip()
            w.writerow(
                {
                    "rank": i,
                    "platform": row.get("platform", ""),
                    "title": title,
                    "link": link,
                    "interactions": row.get("interactions", 0),
                    "impressions": imp if isinstance(imp, int) and imp >= 0 else "",
                    "created_time": row.get("created_time", ""),
                    "post_id": row.get("post_id", ""),
                    "text": (row.get("text") or "").replace("\n", " ").strip(),
                }
            )


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    top, info = fetch_womens_health_organic_ranked(top_n=5, max_scan=400)
    if info.get("errors") and not top and not info.get("page_id"):
        for line in info["errors"]:
            print(line, file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "page_id": info.get("page_id"),
                "ig_user_id": info.get("ig_user_id"),
                "matched_in_scan": info.get("matched_count"),
            },
            indent=2,
        )
    )
    for err in info.get("errors") or []:
        print(err, file=sys.stderr)

    if top:
        _write_csv(top, _OUTPUT_CSV)
        print(f"\nWrote {_OUTPUT_CSV}")

    for i, row in enumerate(top, 1):
        preview = (row["text"] or "")[:200].replace("\n", " ")
        print(f"\n--- Rank {i} ({row['platform']}) ---")
        print(f"interactions: {row['interactions']}")
        imp = row["impressions"]
        print(f"impressions: {imp if imp >= 0 else 'n/a'}")
        print(f"title: {row.get('title') or _post_title(row.get('text') or '')}")
        print(f"created: {row['created_time']}")
        print(f"link: {row.get('link') or row.get('permalink', '')}")
        print(f"preview: {preview}")

    if not top:
        print(
            "\nNo organic posts matched the women's health keyword set in the scanned window.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
