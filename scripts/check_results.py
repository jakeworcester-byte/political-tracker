#!/usr/bin/env python3
"""Health check for the results parser (stdlib only). Run on election night before the morning update.

1. Regression: parses the final 2024 House and Senate Wikipedia pages and checks the known outcome
   (House 435 seats, R 220 / D 215; Senate 35 races, D 18 / R 15 / I 2).
2. Live: parses the 2026 pages from config/postelection.json and reports rows parsed, rows called,
   rows with vote shares, and anything suspicious:
   * a table that has a Candidates column but was not recognized (a layout change)
   * a row with a winner mark ("Y" before a name) that was not counted as called
   * a row counted as called with no winner identified
Exit code 0 = healthy, 1 = something to fix (details printed). Never writes repository files.
Usage: python3 scripts/check_results.py [--skip-2024] [--html-dir DIR]
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import results as R  # noqa: E402

PAGES_2024 = {"house": "https://en.wikipedia.org/wiki/2024_United_States_House_of_Representatives_elections",
              "senate": "https://en.wikipedia.org/wiki/2024_United_States_Senate_elections"}
EXPECT_2024 = {"house": (435, {"R": 220, "D": 215}), "senate": (35, {"D": 18, "R": 15, "I": 2})}


def get(url):
    status, body = collect.fetch(url, accept="text/html")
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    time.sleep(2)
    return body.decode("utf-8", "replace")


def parse(which, html):
    if which == "house":
        return R.parse_house(html)
    return R.parse_senate(html)[0]


def unrecognized_tables(which, html, parsed):
    """Tables with a Candidates header whose rows did not end up in the parse."""
    p = collect.TableParser()
    p.feed(html)
    names = {r["name"] for r in parsed.values()}
    out = []
    for i, t in enumerate(p.tables):
        grid = R.expand(t)
        if len(grid) < 3:
            continue
        head = " | ".join(grid[0][:8]) + " || " + " | ".join(grid[1][:8])
        if "candidates" not in head.lower():
            continue
        if "this race" in " ".join(grid[0]).lower():
            continue  # earlier special elections, skipped on purpose
        hits = 0
        for row in grid[2:]:
            loc = row[0] if row else ""
            key = R.house_key(loc)[0] if which == "house" else R.senate_key(loc, False)[0]
            if key and (key in parsed or key + "-S" in parsed):
                hits += 1
        if hits == 0:
            out.append(f"table {i} ({len(grid) - 2} rows) not used; headers: {head[:160]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-2024", action="store_true")
    ap.add_argument("--html-dir", help="read house.html / senate.html for 2026 from this directory")
    args = ap.parse_args()
    bad = []

    if not args.skip_2024:
        for which, url in PAGES_2024.items():
            try:
                races = parse(which, get(url))
            except Exception as e:  # noqa: BLE001
                print(f"[2024 {which}] could not fetch/parse: {e} (regression skipped)")
                continue
            won = collections.Counter(r["winner"]["party"] for r in races.values() if r.get("called") and r.get("winner"))
            n, exp = EXPECT_2024[which]
            ok = len(races) == n and all(won.get(p, 0) == c for p, c in exp.items())
            print(f"[2024 {which}] {len(races)} rows, winners {dict(won)} -> {'OK' if ok else 'MISMATCH, expected ' + str(n) + ' ' + str(exp)}")
            if not ok:
                bad.append(f"2024 {which} regression mismatch")

    for which in ("house", "senate"):
        try:
            html = open(os.path.join(args.html_dir, f"{which}.html"), encoding="utf-8").read() if args.html_dir else get(R.CFG["sources"][which])
        except Exception as e:  # noqa: BLE001
            print(f"[2026 {which}] FETCH FAILED: {e}")
            bad.append(f"2026 {which} fetch failed")
            continue
        races = parse(which, html)
        expected = 435 if which == "house" else 35
        called = [k for k, r in races.items() if r["called"]]
        shares = [k for k, r in races.items() if any(c.get("pct") is not None for c in r["candidates"])]
        won = collections.Counter(races[k]["winner"]["party"] for k in called if races[k].get("winner"))
        print(f"[2026 {which}] {len(races)}/{expected} rows, {len(called)} called {dict(won)}, {len(shares)} with vote shares")
        if len(races) < expected * 0.95:
            bad.append(f"2026 {which}: only {len(races)} of {expected} rows parsed")
        for k, r in races.items():
            if not r["called"] and any(c.get("winner") for c in r["candidates"]):
                bad.append(f"{k}: winner mark present but not counted as called")
            if r["called"] and not r.get("winner"):
                bad.append(f"{k}: called but no winner identified (status: {r['status_text'][:80]})")
            if r.get("winner") and r["winner"]["party"] not in ("D", "R", "I"):
                print(f"  note {k}: winner party '{r['winner'].get('party_label')}' maps to Other")
        for msg in unrecognized_tables(which, html, races):
            print(f"  {msg}")
            bad.append(f"2026 {which}: {msg[:90]}")

    if bad:
        print("\nNEEDS ATTENTION:")
        for b in bad:
            print(f"- {b}")
        return 1
    print("\nparser healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
