#!/usr/bin/env python3
"""Alaska Division of Elections: the state's own unofficial count for the U.S. Senate and House races.

Wikipedia lags the state, and Alaska's first-choice count decides whether ranked-choice tabulation is
needed, so the routine reads the state's Election Summary Report (PDF) directly every morning, plus the
precinct CSV for the split by ballot type (election day, early, absentee, questioned).

Writes data/raw/<date>/alaska_state.json and inserts an "Alaska state count" section into
data/bundle/<date>.md (before the CourtListener section if that is already there). Standard library only.
Never exits non-zero: a failed fetch is reported in the section instead.

Usage: python3 scripts/alaska.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import io
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402
import results as R  # noqa: E402

ROOT = R.ROOT
MARKER = "\n## Alaska state count (Division of Elections)"
COURTS_MARKER = "\n## Court dockets (CourtListener)"
CFG = R.CFG.get("alaska_state") or {}
VOTE_TYPES = [("Election Day", "election day"), ("Early Voting", "early"), ("Absentee", "absentee"), ("Question", "questioned")]


def num(s):
    return int(s.replace(",", ""))


def pdf_tokens(data):
    """Text strings from the PDF's content streams, in stream order. The report writes plain literal
    strings with Tj, so inflating each stream and reading the strings is enough."""
    toks = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            s = zlib.decompress(m.group(1))
        except Exception:  # noqa: BLE001  (images, fonts)
            continue
        for t in re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            t = re.sub(rb"\\(.)", rb"\1", t).decode("latin-1").strip()
            if t:
                toks.append(t)
    return toks


def pdf_contests(toks):
    """Candidate tables: rows of name, optional party, votes, percent; ended by 'Total Votes', total."""
    blocks, i = [], 0
    while i < len(toks):
        if toks[i:i + 3] == ["Candidate", "Party", "Total"]:
            i += 3
            rows = []
            while i < len(toks) and toks[i] != "Total Votes":
                name = toks[i]; i += 1
                party = ""
                if i < len(toks) and not re.fullmatch(r"[\d,]+", toks[i]):
                    party = toks[i]; i += 1
                if i + 1 < len(toks) and re.fullmatch(r"[\d,]+", toks[i]) and toks[i + 1].endswith("%"):
                    rows.append({"name": name, "party": party, "votes": num(toks[i]), "pct": float(toks[i + 1].rstrip("%"))})
                    i += 2
                else:
                    break
            if i + 1 < len(toks) and toks[i] == "Total Votes" and re.fullmatch(r"[\d,]+", toks[i + 1]):
                blocks.append({"rows": rows, "total": num(toks[i + 1])})
                i += 2
            continue
        i += 1
    return blocks


def csv_by_type(text, contest_title):
    """Votes per candidate and ballot type for one contest, summed over precincts."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("Contest_title") != contest_title:
            continue
        c = out.setdefault(r["candidate_name"], {k: 0 for _, k in VOTE_TYPES})
        for col, k in VOTE_TYPES:
            c[k] += int(r.get(f"{col}_votes") or 0)
    return out


def summarize(block):
    rows = sorted(block["rows"], key=lambda r: -r["votes"])
    total = block["total"]
    lead, second = rows[0], rows[1]
    majority = total // 2 + 1  # strictly more than half of all first-choice votes, write-ins included
    return {"rows": rows, "total": total, "leader": lead["name"], "runner_up": second["name"],
            "margin_votes": lead["votes"] - second["votes"], "margin_pts": round(lead["pct"] - second["pct"], 2),
            "leader_pct": lead["pct"], "majority_votes": majority,
            "leader_short_of_majority": max(0, majority - lead["votes"])}


def previous(run_date):
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "20*", "alaska_state.json")), reverse=True):
        if os.path.basename(os.path.dirname(f)) < run_date:
            return R.load(f, None)
    return None


def write_digest(run_date, rep):
    path = os.path.join(ROOT, "data", "bundle", f"{run_date}.md")
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else f"# Tracker digest for {run_date}\n"
    if MARKER in text:  # rerun: drop our old section, keep anything after it
        head, rest = text.split(MARKER, 1)
        nxt = rest.find("\n## ")
        text = head.rstrip() + "\n" + (rest[nxt:] if nxt >= 0 else "")
    L = [MARKER.strip(), ""]
    L.append(f"Source: {CFG.get('summary_pdf')} (official unofficial-results report) and {CFG.get('precinct_csv')}. "
             "This is the state's own count; prefer it to the Wikipedia table for Alaska. First-choice shares include write-ins, as the state reports them. "
             "A candidate with a majority of first-choice votes wins outright; otherwise the Division runs ranked-choice tabulation.")
    if rep.get("error"):
        L.append(f"- Could not read the state report: {rep['error']}. Use news leads for Alaska and say so in the Coverage note.")
    else:
        L.append(f"- Report timestamp: {rep['report_time']} (Alaska time)." + (
            f" Same report as the last run ({rep['prev_date']}): no new count since then." if rep.get("unchanged") else
            (f" New count since the last run ({rep['prev_date']}, report {rep['prev_report_time']})." if rep.get("prev_date") else "")))
        if CFG.get("next_events"):
            L.append("- Scheduled (from the Division's results page): " + "; ".join(CFG["next_events"]))
        for key, c in rep["contests"].items():
            rows = "; ".join(f"{r['name']}{' (' + r['party'] + ')' if r['party'] else ''} {r['votes']:,} ({r['pct']}%)" for r in c["rows"])
            L.append(f"- {key}: {rows}; total {c['total']:,}.")
            maj = (f"{c['leader']} has a first-choice majority." if c["leader_short_of_majority"] == 0 else
                   f"{c['leader']} is {c['leader_short_of_majority']:,} votes short of a first-choice majority ({c['majority_votes']:,} of {c['total']:,}).")
            L.append(f"  - Lead {c['margin_votes']:,} votes ({c['margin_pts']} pts). {maj}")
            if c.get("change"):
                ch = c["change"]
                L.append(f"  - Since the last report: {ch['new_votes']:,} new votes; {c['leader']} took {ch['leader_share_of_new']}% of them; lead changed by {ch['margin_change']:+,}.")
            if c.get("by_type"):
                parts = []
                for _, k in VOTE_TYPES:
                    tot = sum(v[k] for v in c["by_type"].values())
                    if tot:
                        parts.append(f"{k} {round(100 * c['by_type'].get(c['leader'], {}).get(k, 0) / tot, 1)}% of {tot:,}")
                L.append(f"  - {c['leader']}'s share by ballot type counted so far (named candidates only; context, not a projection): " + "; ".join(parts))
    section = "\n".join(L) + "\n"
    if COURTS_MARKER in text:
        a, b = text.split(COURTS_MARKER, 1)
        text = a.rstrip() + "\n\n" + section + COURTS_MARKER + b
    else:
        text = text.rstrip() + "\n\n" + section
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    args = ap.parse_args()
    run_date = args.date or R.chicago_now().date().isoformat()
    rep = {"date": run_date, "contests": {}}
    try:
        st, pdf = collect.fetch(CFG["summary_pdf"], accept="application/pdf")
        if st != 200:
            raise RuntimeError(f"HTTP {st} for the summary PDF")
        toks = pdf_tokens(pdf)
        rep["report_time"] = next((t for t in toks if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M", t)), None)
        blocks = pdf_contests(toks)
        csv_text = None
        try:
            st2, body = collect.fetch(CFG["precinct_csv"], accept="text/csv")
            csv_text = body.decode("utf-8-sig", "replace") if st2 == 200 else None
        except Exception:  # noqa: BLE001  (the split by ballot type is optional)
            csv_text = None
        for key, spec in CFG.get("contests", {}).items():
            block = next((b for b in blocks if any(r["name"] == spec["candidate"] for r in b["rows"])), None)
            if not block:
                raise RuntimeError(f"no table with {spec['candidate']} in the summary PDF")
            c = summarize(block)
            if csv_text:
                c["by_type"] = csv_by_type(csv_text, spec["csv_title"])
            rep["contests"][key] = c
        prev = previous(run_date)
        if prev and not prev.get("error"):
            rep["prev_date"], rep["prev_report_time"] = prev["date"], prev.get("report_time")
            rep["unchanged"] = prev.get("report_time") == rep["report_time"]
            for key, c in rep["contests"].items():
                pc = (prev.get("contests") or {}).get(key)
                if pc and not rep["unchanged"]:
                    old = {r["name"]: r["votes"] for r in pc["rows"]}
                    new_votes = c["total"] - pc["total"]
                    lead_gain = next(r["votes"] for r in c["rows"] if r["name"] == c["leader"]) - old.get(c["leader"], 0)
                    if new_votes > 0:
                        c["change"] = {"new_votes": new_votes, "leader_share_of_new": round(100 * lead_gain / new_votes, 1),
                                       "margin_change": c["margin_votes"] - pc["margin_votes"]}
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"{type(e).__name__}: {e}"
    out = os.path.join(ROOT, "data", "raw", run_date)
    os.makedirs(out, exist_ok=True)
    R.dump(rep, os.path.join(out, "alaska_state.json"))
    write_digest(run_date, rep)
    if rep.get("error"):
        print(f"alaska: FAILED ({rep['error']})")
    else:
        s = rep["contests"].get("SEN-AK")
        print(f"alaska: report {rep['report_time']}" + (" (unchanged)" if rep.get("unchanged") else "")
              + (f"; SEN-AK leader {s['leader_pct']}%, {s['leader_short_of_majority']} short of majority" if s else ""))


if __name__ == "__main__":
    main()
