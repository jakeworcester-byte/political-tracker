#!/usr/bin/env python3
"""Fold the day's brief into the site data.

The routine (Claude) writes the morning brief to data/briefs/<date>.md after reading
data/bundle/<date>.md. This script then:
  * stores the brief inside docs/data/latest.json (so the site home page shows it)
  * copies it to docs/data/briefs/<date>.md and refreshes docs/data/briefs/index.json
  * validates the brief against the rubric's hard rules (URL-backed claims, required sections)

From config/postelection.json's post_election_from date the brief is an election update with its own
required sections and a longer word budget, and --edition pm publishes the evening edition from
data/briefs/<date>-pm.md (archived as docs/data/briefs/<date>-pm.md).

Usage: python3 scripts/publish.py [--date YYYY-MM-DD] [--edition am|pm] [--strict]
Exit code 3 means the brief failed validation; the site is still updated unless --strict.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED_SECTIONS = ["Bottom line", "What moved", "Races to watch", "What the spectrum is saying", "Talking points", "Coverage note"]
POST_SECTIONS = ["Bottom line", "Called since", "Still uncalled", "Recounts and certification", "Legal and federal action", "Coverage note"]


def post_election_from():
    try:
        return json.load(open(os.path.join(ROOT, "config", "postelection.json"), encoding="utf-8"))["post_election_from"]
    except (OSError, KeyError, ValueError):
        return "9999-12-31"


def validate(md, post=False):
    problems = []
    for sec in (POST_SECTIONS if post else REQUIRED_SECTIONS):
        if not re.search(r"^#{1,3}\s*" + re.escape(sec), md, re.I | re.M) and not re.search(r"\*\*" + re.escape(sec), md, re.I):
            problems.append(f"missing section: {sec}")
    urls = re.findall(r"https?://\S+", md)
    if len(urls) < 3:
        problems.append(f"only {len(urls)} URLs cited; every factual claim needs a source link")
    words = len(md.split())
    cap, target = 900, "under 600 for a two-minute phone read"
    if post:
        # The Still uncalled section gives every uncalled race one line, so it is exempt from the word budget.
        words = len(re.sub(r"^##\s*Still uncalled.*?(?=^##\s)", "", md, flags=re.I | re.M | re.S).split())
        cap, target = 1000, "under 800 outside the Still uncalled section"
    if words > cap:
        problems.append(f"brief is {words} words; target is {target}")
    for bad in ("I predict", "I expect", "we predict", "my forecast"):
        if bad.lower() in md.lower():
            problems.append(f"forbidden phrasing: '{bad}' (not a forecast)")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--edition", choices=["am", "pm"], default="am")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    run_date = args.date or now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    post = run_date >= post_election_from()
    slug = run_date + ("-pm" if args.edition == "pm" else "")
    brief_path = os.path.join(ROOT, "data", "briefs", f"{slug}.md")
    if not os.path.exists(brief_path):
        print(f"no brief at {brief_path}", file=sys.stderr)
        return 2
    md = open(brief_path, encoding="utf-8").read().strip()
    problems = validate(md, post)
    for p in problems:
        print(f"[brief check] {p}", file=sys.stderr)
    if problems and args.strict:
        return 3

    latest_path = os.path.join(ROOT, "docs", "data", "latest.json")
    latest = json.load(open(latest_path, encoding="utf-8"))
    title_m = re.search(r"^#\s+(.*)$", md, re.M)
    latest["brief"] = {"date": run_date, "title": title_m.group(1).strip() if title_m else f"Morning brief, {run_date}",
                       "edition": args.edition, "slug": slug, "markdown": md, "published_at": now.isoformat(), "checks": problems}
    json.dump(latest, open(latest_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    bdir = os.path.join(ROOT, "docs", "data", "briefs")
    os.makedirs(bdir, exist_ok=True)
    open(os.path.join(bdir, f"{slug}.md"), "w", encoding="utf-8").write(md + "\n")
    index = sorted([f[:-3] for f in os.listdir(bdir) if f.endswith(".md")], reverse=True)
    json.dump({"dates": index, "latest": index[0] if index else None}, open(os.path.join(bdir, "index.json"), "w"), indent=1)
    print(f"published brief {slug} ({'election update' if post else 'pre-election'}, {len(md.split())} words, {len(problems)} check notes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
