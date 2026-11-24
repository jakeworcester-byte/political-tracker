#!/usr/bin/env python3
"""Health check for the results parser (stdlib only). Run on election night before the morning update.

1. Regression: parses the final 2024 House and Senate Wikipedia pages and checks the known outcome
   (House 435 seats, R 220 / D 215; Senate 35 races, D 18 / R 15 / I 2).
2. Live: parses the 2026 pages from config/postelection.json and reports rows parsed, rows called,
   rows with vote shares, and anything suspicious:
   * fewer rows than expected (House 435, Senate 35, governors 36)
   * districts or states the last snapshots had that today's parse lost, named by state
   * a table with a Candidate(s) column whose districts are missing from the parse (a layout change)
   * a row with a winner mark ("Y" before a name) that was not counted as called
   * a row counted as called with no winner identified
Exit code 0 = healthy, 1 = something to fix (details printed). Never writes repository files.
Usage: python3 scripts/check_results.py [--skip-2024] [--html-dir DIR]   (DIR holds house.html, senate.html, governors.html)
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
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


EXPECTED = {"house": 435, "senate": 35, "governors": 36}


def parse(which, html):
    if which == "house":
        return R.parse_house(html)
    if which == "governors":
        return {f"GOV-{a}": r for a, r in R.parse_governors(html)[0].items()}
    return R.parse_senate(html)[0]


def snapshot_keys(which, n=3):
    """Race keys for one office in the last n results snapshots (data/results/*.json), unioned."""
    keys = set()
    for f in sorted(glob.glob(os.path.join(R.ROOT, "data", "results", "20*.json")))[-n:]:
        try:
            snap = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if which == "governors":
            st = (snap.get("governors") or {}).get("states") or {}
            keys |= {f"GOV-{a}" for a, v in st.items() if v.get("up")}
        else:
            chamber = "House" if which == "house" else "Senate"
            keys |= {k for k, r in (snap.get("races") or {}).items() if r.get("chamber") == chamber}
    return keys


def by_state(keys):
    """'HOU-PA-07' -> grouped as 'PA (17): PA-01, ...' for a readable message."""
    groups = collections.defaultdict(list)
    for k in sorted(keys):
        parts = k.split("-")
        groups[parts[1] if len(parts) > 1 else k].append(k)
    return "; ".join(f"{st} ({len(v)})" for st, v in sorted(groups.items()))


def unrecognized_tables(which, html, parsed):
    """Tables with a Candidate(s) header holding districts or states that did not end up in the parse.
    Judged by the rows, not the headings: a table is only skipped when none of its rows is a real
    district or state (e.g. the non-voting delegates) or all of them are in the parse anyway
    (e.g. the earlier special elections, whose districts the regular tables also cover)."""
    p = collect.TableParser()
    p.feed(html)
    names = {r["name"] for r in parsed.values()}
    out = []
    for i, t in enumerate(p.tables):
        grid = R.expand(t)
        if len(grid) < 3:
            continue
        head = " | ".join(grid[0][:8]) + " || " + " | ".join(grid[1][:8])
        if "candidate" not in head.lower():
            continue
        missing = set()
        for row in grid[2:]:
            loc = row[0] if row else ""
            key = R.house_key(loc)[0] if which == "house" else R.senate_key(loc, False)[0]
            if key and key not in parsed and key + "-S" not in parsed:
                missing.add(key)
        if missing:
            out.append(f"table {i} ({len(grid) - 2} rows): {len(missing)} not parsed, {by_state(missing)}; headers: {head[:120]}")
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

    gov_final = R.CFG.get("governors_final") and os.path.exists(os.path.join(R.ROOT, R.CFG["governors_final"]))
    if gov_final:
        print(f"[2026 governors] results are final ({R.CFG['governors_final']}); table not checked")
    for which in ("house", "senate") + (() if gov_final else ("governors",)):
        try:
            html = open(os.path.join(args.html_dir, f"{which}.html"), encoding="utf-8").read() if args.html_dir else get(R.CFG["sources"][which])
        except Exception as e:  # noqa: BLE001
            print(f"[2026 {which}] FETCH FAILED: {e}")
            bad.append(f"2026 {which} fetch failed")
            continue
        races = parse(which, html)
        expected = EXPECTED[which]
        called = [k for k, r in races.items() if r["called"]]
        shares = [k for k, r in races.items() if any(c.get("pct") is not None for c in r["candidates"])]
        won = collections.Counter(races[k]["winner"]["party"] for k in called if races[k].get("winner"))
        print(f"[2026 {which}] {len(races)}/{expected} rows, {len(called)} called {dict(won)}, {len(shares)} with vote shares")
        if len(races) < expected:
            bad.append(f"2026 {which}: only {len(races)} of {expected} rows parsed")
        prior = snapshot_keys(which)
        lost = prior - set(races)
        if lost and lost == prior:
            print(f"  none of the {len(prior)} races in recent snapshots parsed today: the table layout has likely changed")
            bad.append(f"2026 {which}: none of the {len(prior)} races in recent snapshots parsed today")
        elif lost:
            print(f"  in recent snapshots but not parsed today: {len(lost)}, {by_state(lost)}")
            bad.append(f"2026 {which}: {len(lost)} races from recent snapshots missing today ({by_state(lost)[:120]})")
        runoffs = sorted(k for k, r in races.items() if r.get("runoff") and not r["runoff"].get("decided"))
        if runoffs:
            print(f"  runoff pending (advancers marked, not counted as called): {', '.join(runoffs)}")
        for k, r in races.items():
            marked = {c["name"] for c in r["candidates"] if c.get("winner")}
            if r["called"] and len(marked) > 1 and not (r.get("runoff") or {}).get("decided"):
                bad.append(f"{k}: counted as called but {len(marked)} candidates carry a winner mark (runoff or top-two row?)")
            if not r["called"] and marked and not r.get("runoff"):
                bad.append(f"{k}: winner mark present but not counted as called")
            if r["called"] and not r.get("winner"):
                bad.append(f"{k}: called but no winner identified (status: {r['status_text'][:80]})")
            if r.get("winner") and r["winner"]["party"] not in ("D", "R", "I"):
                print(f"  note {k}: winner party '{r['winner'].get('party_label')}' maps to Other")
        for msg in (unrecognized_tables(which, html, races) if which != "governors" else []):
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
