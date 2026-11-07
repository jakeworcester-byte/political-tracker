#!/usr/bin/env python3
"""Post-election: recent election-related court dockets and opinions from CourtListener.

Runs after results.py. The token can come from either place:
  * a network secret for www.courtlistener.com saved on the cloud environment (the session proxy adds
    the Authorization header, so the script sends a placeholder that the proxy replaces), or
  * the environment variable named in config/postelection.json -> courtlistener.token_env
    (default COURTLISTENER_TOKEN).
With neither, the first request comes back 401 and the script writes a one-line "not used" note into
the digest and exits 0, so the run never fails on it.

The free tier allows 5 requests a minute, 50 an hour and 125 a day. Each run makes at most
`max_requests_per_run` search calls (default 5), spaced `min_seconds_between_requests` apart, so
two runs a day use about 10 of the 125.

Output: data/courts/<date>.json and a "Court dockets (CourtListener)" section appended to
data/bundle/<date>.md. Standard library only.

    python3 scripts/courts.py [--date YYYY-MM-DD] [--fixture-dir DIR]

--fixture-dir reads r-<n>.json / o-<n>.json saved search responses instead of calling the API (testing).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config", "postelection.json"), encoding="utf-8")).get("courtlistener", {})
API = "https://www.courtlistener.com/api/rest/v4/search/"
SITE = "https://www.courtlistener.com"
UA = "JakesPoliticalTracker/0.1 (courtlistener)"
MARKER = "\n## Court dockets (CourtListener)"


def chicago_today():
    now = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Chicago")).date()
    except Exception:  # noqa: BLE001
        return now.astimezone(dt.timezone(dt.timedelta(hours=-6))).date()


def absolute(url):
    if not url:
        return ""
    return url if url.startswith("http") else SITE + url


def clean(text, n=160):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def search(token, params=None, url=None):
    """One search call (by params, or by a full `next` page URL). Returns (status, parsed JSON or None, error text)."""
    url = url or API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}", "User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace") if e.fp else ""
        return e.code, None, body
    except Exception as e:  # noqa: BLE001
        return 0, None, str(e)


def skip_docket(r):
    """Drop bankruptcy courts (court ids end in 'b', e.g. njb, ganb) and suit types in skip_suit_natures."""
    court_id = (r.get("court_id") or "").lower()
    if court_id.endswith("b"):
        return True
    nature = r.get("suitNature") or ""
    return any(s.lower() in nature.lower() for s in CFG.get("skip_suit_natures", []))


def docket_items(data, since):
    """type=r results: dockets with up to three nested matching documents."""
    out = []
    for r in (data or {}).get("results", []):
        if skip_docket(r):
            continue
        docs = r.get("recap_documents") or r.get("documents") or []
        entries = []
        for d in docs:
            filed = (d.get("entry_date_filed") or "")[:10]
            if filed and filed < since:
                continue
            entries.append({"date": filed, "entry_number": d.get("entry_number"),
                            "description": clean(d.get("short_description") or d.get("description") or d.get("snippet"), 200),
                            "url": absolute(d.get("absolute_url"))})
        docket_url = absolute(r.get("docket_absolute_url") or r.get("absolute_url")
                              or (f"/docket/{r['docket_id']}/" if r.get("docket_id") else ""))
        out.append({"case": clean(r.get("caseName") or r.get("case_name"), 140), "court": r.get("court") or r.get("court_id") or "",
                    "docket_number": r.get("docketNumber") or "", "date_filed": (r.get("dateFiled") or "")[:10],
                    "suit_nature": r.get("suitNature") or "",
                    "url": docket_url, "entries": entries, "more_docs": bool(r.get("more_docs"))})
    return out


def opinion_items(data):
    """type=o results: opinion clusters with nested opinions carrying a snippet."""
    out = []
    for r in (data or {}).get("results", []):
        snippet = ""
        for o in r.get("opinions") or []:
            if o.get("snippet"):
                snippet = clean(o["snippet"], 220)
                break
        out.append({"case": clean(r.get("caseName"), 140), "court": r.get("court") or r.get("court_id") or "",
                    "docket_number": r.get("docketNumber") or "", "date_filed": (r.get("dateFiled") or "")[:10],
                    "url": absolute(r.get("absolute_url")), "snippet": snippet})
    return out


def write_digest(run_date, report):
    path = os.path.join(ROOT, "data", "bundle", f"{run_date}.md")
    existing = open(path, encoding="utf-8").read() if os.path.exists(path) else f"# Tracker digest for {run_date}\n"
    existing = existing.split(MARKER)[0].rstrip() + "\n"
    L = [MARKER + "\n"]
    status = report["status"]
    if status == "no_token":
        L.append(f"- Not used: no API key (no network secret for www.courtlistener.com and no ${report['token_env']}). Legal developments come from the news leads only; "
                 "say 'CourtListener not used (no API token)' in the Coverage note.")
    elif status == "disabled":
        L.append("- Not used: disabled in config/postelection.json. Say 'CourtListener not used (disabled)' in the Coverage note.")
    elif status == "auth_failed":
        L.append(f"- Not used: the token in ${report['token_env']} was refused (HTTP {report.get('http_status')}: "
                 f"{clean(report.get('error'), 120)}). Legal developments come from the news leads only; say "
                 "'CourtListener not used (token refused)' in the Coverage note.")
    else:
        L.append(f"Federal (PACER/RECAP) dockets and court opinions matching election terms, with activity since {report['since']}. "
                 "These are leads, not findings: open the docket or opinion before citing it, describe filings neutrally "
                 "(who filed what, what the court ordered), and cite the CourtListener link. A docket with no new entry shown "
                 "may have newer filings than CourtListener has (it only holds documents someone bought from PACER). "
                 f"Requests used this run: {report['requests']}.")
        if status == "rate_limited":
            L.append("- Stopped early: CourtListener returned HTTP 429 (rate limit). Results below are partial; say so in the Coverage note.")
        for err in report.get("errors", []):
            L.append(f"- Query failed: {err}")
        seen = set()
        L.append("\n### Dockets with recent filings")
        n = 0
        for q in report["dockets"]:
            for it in q["items"]:
                if it["url"] in seen:
                    continue
                seen.add(it["url"])
                n += 1
                nature = f" | {it['suit_nature']}" if it.get("suit_nature") else ""
                L.append(f"- {it['case']} | {it['court']} | No. {it['docket_number'] or '?'} | filed {it['date_filed'] or '?'}{nature} | {it['url']}")
                for e in it["entries"]:
                    num = f"#{e['entry_number']} " if e.get("entry_number") else ""
                    L.append(f"  - entry {num}{e['date'] or '?'}: {e['description'] or '(no description)'}"
                             + (f" | {e['url']}" if e.get("url") else ""))
                if it.get("more_docs"):
                    L.append("  - more matching documents on the docket page")
        if n == 0:
            L.append("- none in the lookback window")
        L.append("\n### Opinions")
        n = 0
        for q in report["opinions"]:
            for it in q["items"]:
                if it["url"] in seen:
                    continue
                seen.add(it["url"])
                n += 1
                L.append(f"- {it['case']} | {it['court']} | No. {it['docket_number'] or '?'} | filed {it['date_filed'] or '?'} | {it['url']}"
                         + (f"\n  - {it['snippet']}" if it.get("snippet") else ""))
        if n == 0:
            L.append("- none in the lookback window")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(existing + "\n".join(L) + "\n")


def load_fixture(fixture_dir, kind, n):
    p = os.path.join(fixture_dir, f"{kind}-{n}.json")
    if not os.path.exists(p):
        return 404, None, f"fixture {p} missing"
    return 200, json.load(open(p, encoding="utf-8")), ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: today in America/Chicago)")
    ap.add_argument("--fixture-dir", help="read saved search responses instead of calling the API")
    args = ap.parse_args()
    run_date = args.date or chicago_today().isoformat()
    token_env = CFG.get("token_env", "COURTLISTENER_TOKEN")
    lookback = int(CFG.get("lookback_days", 3))
    since = (dt.date.fromisoformat(run_date) - dt.timedelta(days=lookback)).isoformat()
    max_req = int(CFG.get("max_requests_per_run", 5))
    gap = float(CFG.get("min_seconds_between_requests", 13))
    report = {"date": run_date, "since": since, "token_env": token_env, "requests": 0,
              "status": "ok", "errors": [], "dockets": [], "opinions": []}

    env_token = os.environ.get(token_env, "").strip()
    # No env var: send a placeholder; a network secret on the environment makes the proxy swap in the real key.
    token = env_token or "proxy-injected-placeholder"
    if not CFG.get("enabled", True):
        report["status"] = "disabled"
    else:
        # One call per query first; leftover budget then follows `next` pages of docket queries.
        plan = [("r", q, None) for q in CFG.get("docket_queries", [])] + [("o", q, None) for q in CFG.get("opinion_queries", [])]
        counters = {"r": 0, "o": 0}
        while plan:
            kind, q, next_url = plan.pop(0)
            if report["requests"] >= max_req:
                break
            counters[kind] += 1
            if kind == "r":
                params = {"type": "r", "q": f"({q}) AND entry_date_filed:[{since} TO *]", "order_by": "entry_date_filed desc"}
            else:
                params = {"type": "o", "q": f"({q}) AND dateFiled:[{since} TO *]", "order_by": "dateFiled desc"}
            if args.fixture_dir:
                status, data, err = load_fixture(args.fixture_dir, kind, counters[kind])
            else:
                if report["requests"]:
                    time.sleep(gap)
                status, data, err = search(token, params, url=next_url)
            report["requests"] += 1
            if status in (401, 403):
                if env_token or args.fixture_dir:
                    report.update(status="auth_failed", http_status=status, error=err)
                else:
                    report.update(status="no_token", http_status=status, error=err)
                break
            if status == 429:
                report["status"] = "rate_limited"
                break
            if status != 200 or data is None:
                report["errors"].append(f"{kind} '{q}': HTTP {status} {clean(err, 120)}")
                continue
            bucket = "dockets" if kind == "r" else "opinions"
            items = docket_items(data, since) if kind == "r" else opinion_items(data)
            if kind == "o":
                items = [it for it in items if not it["date_filed"] or it["date_filed"] >= since]
            report[bucket].append({"query": q, "count": data.get("count"), "page2": bool(next_url), "items": items})
            if kind == "r" and data.get("next"):
                plan.append(("r", q, data["next"]))

    out_dir = os.path.join(ROOT, "data", "courts")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{run_date}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    write_digest(run_date, report)

    if report["status"] == "no_token":
        print(f"courts: skipped, no API key (no network secret and ${token_env} is not set)")
    elif report["status"] == "disabled":
        print("courts: skipped, disabled in config")
    elif report["status"] == "auth_failed":
        print(f"courts: auth_failed, HTTP {report.get('http_status')} {clean(report.get('error'), 120)}")
    else:
        nd = sum(len(q["items"]) for q in report["dockets"])
        no = sum(len(q["items"]) for q in report["opinions"])
        print(f"courts: {report['status']}, {report['requests']} requests, {nd} dockets, {no} opinions, "
              f"{len(report['errors'])} errors (since {since})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001  (never fail the run)
        print(f"courts: unexpected error, skipped: {e}")
        sys.exit(0)
