#!/usr/bin/env python3
"""Daily early vote tracker: early ballots by party versus party registration.

Party registration is not party vote. Everything here is a participation signal:
who is returning ballots early, compared with who is registered, and compared with
the same point in a prior cycle when a source reports it.

Inputs
  * North Carolina: fetched automatically from the State Board of Elections daily
    PDF (registration by party, ballots by party, and the same-point 2022 total).
    The State Board's raw absentee file, a day fresher, supplies the ballot counts when newer.
  * Every other state with party data: the UF Election Lab's published CSV.
  * data/early_vote/research/<date>.json, written by the routine's bounded research step
    (see ROUTINE_PROMPT.md), overrides UF for a state, e.g. to add a same-point prior-cycle figure.
  * Registration baselines: config/early_vote.json.

Outputs
  * data/raw/<date>/early_vote_nc.json        raw NC parse, for diagnosis
  * docs/data/early_vote.json                 latest per state + daily history (the site reads this)
  * a "## Early vote" section appended to data/bundle/<date>.md for the brief writer

Standard library only. pdftotext is used when the system has it; otherwise a
pure-Python text extractor reads the PDF's compressed content streams.

Usage: python3 scripts/early_vote.py [--date YYYY-MM-DD] [--skip-fetch]
Never fails the morning run: problems are recorded and the script exits 0.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "config", "early_vote.json")
SOURCES = json.load(open(os.path.join(ROOT, "config", "sources.json"), encoding="utf-8"))
UA = SOURCES.get("user_agent", "JakesPoliticalTracker/0.1")
OUT_PATH = os.path.join(ROOT, "docs", "data", "early_vote.json")
PARTIES = ("D", "R", "Other")


def chicago_today():
    # Central time: UTC-5 in daylight time, which covers the whole early-vote season.
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


# ---------------------------------------------------------------- PDF text
def pdf_text_stdlib(data: bytes) -> str:
    """Rough text extraction from FlateDecode content streams (Tj / TJ operators)."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            body = zlib.decompress(raw)
        except zlib.error:
            continue
        if b"BT" not in body:
            continue
        line = []
        for tok in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(.*?)\]\s*TJ|T\*|Td|TD|Tm|ET", body, re.S):
            t = tok.group(0)
            if t.endswith(b"Tj"):
                line.append(_pdf_str(t[: t.rfind(b")") + 1]))
            elif t.endswith(b"TJ"):
                parts = re.findall(rb"\((?:\\.|[^\\)])*\)|-?\d+\.?\d*", tok.group(1))
                s = ""
                for p in parts:
                    if p.startswith(b"("):
                        s += _pdf_str(p)
                    elif float(p) < -200:
                        s += " "
                line.append(s)
            else:
                if line:
                    out.append("".join(line))
                    line = []
        if line:
            out.append("".join(line))
    return "\n".join(out)


def _pdf_str(lit: bytes) -> str:
    s = lit[1:-1]
    s = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b""}.get(m.group(1), m.group(1)), s)
    s = re.sub(rb"\\(\d{1,3})", lambda m: bytes([int(m.group(1), 8) & 255]), s)
    return s.decode("latin-1")


def pdf_texts(data: bytes):
    """Yield candidate text renderings, best first."""
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "in.pdf")
            open(p, "wb").write(data)
            for flags in ([], ["-layout"], ["-raw"]):
                try:
                    yield subprocess.run(["pdftotext", *flags, p, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
                except Exception:
                    pass
    yield pdf_text_stdlib(data)


# ---------------------------------------------------------------- NC parse
NUM = r"([\d,]+)"
PCT = r"([\d.]+)%"


def parse_nc(text: str):
    t = re.sub(r"[ \t]+", " ", text)
    rows = {}
    for party in ("Democratic", "Republican", "Unaffiliated", "Libertarian", "Green"):
        m = re.search(party + r"\s+" + NUM + r"\s+" + PCT + r"\s+" + party + r"\s+" + NUM + r"\s+" + PCT + r"\s+" + PCT, t)
        if not m:
            return None
        rows[party] = {"registered": int(m.group(1).replace(",", "")), "reg_pct": float(m.group(2)),
                       "ballots": int(m.group(3).replace(",", "")), "turnout_pct": float(m.group(4)), "ballot_pct": float(m.group(5))}
    total = re.search(r"Total Ballots Cast\s+" + NUM, t)
    inperson = re.search(r"In-Person Early Voting\s+" + NUM, t)
    prior = re.search(r"Total number of accepted ballots cast\)\s*" + NUM, t)
    through = re.search(r"Ballots cast through\s+(\d{1,2})/(\d{1,2})/(\d{4})", t)
    published = re.search(r"Published\s+(\d{1,2})/(\d{1,2})/(\d{4})", t)
    iso = lambda m: f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None
    other_keys = ("Unaffiliated", "Libertarian", "Green")
    return {
        "as_of": iso(through), "published": iso(published),
        "total": int(total.group(1).replace(",", "")) if total else sum(r["ballots"] for r in rows.values()),
        "in_person": int(inperson.group(1).replace(",", "")) if inperson else None,
        "prior_same_point_total": int(prior.group(1).replace(",", "")) if prior else None,
        "rows": rows,
        "ballots_share": {"D": rows["Democratic"]["ballot_pct"], "R": rows["Republican"]["ballot_pct"],
                          "Other": round(sum(rows[k]["ballot_pct"] for k in other_keys), 2)},
        "registration_share": {"D": rows["Democratic"]["reg_pct"], "R": rows["Republican"]["reg_pct"],
                               "Other": round(sum(rows[k]["reg_pct"] for k in other_keys), 2)},
        "turnout_pct": {"D": rows["Democratic"]["turnout_pct"], "R": rows["Republican"]["turnout_pct"],
                        "Other": rows["Unaffiliated"]["turnout_pct"]},
    }


def collect_nc(cfg, run_date, raw_dir, problems):
    src = cfg["auto_sources"]["NC"]
    for back in range(0, 4):
        d = (run_date - dt.timedelta(days=back)).isoformat()
        url = src["url_template"].format(date=d)
        try:
            status, data = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                continue
            problems.append(f"NC report {d}: HTTP {e.code}")
            continue
        except Exception as e:  # network trouble
            problems.append(f"NC report {d}: {e.__class__.__name__}: {e}")
            continue
        for text in pdf_texts(data):
            parsed = parse_nc(text or "")
            if parsed:
                parsed.update({"source": src["name"], "source_url": url, "measure": "absentee by mail + in-person early ballots cast (accepted)"})
                os.makedirs(raw_dir, exist_ok=True)
                json.dump(parsed, open(os.path.join(raw_dir, "early_vote_nc.json"), "w"), indent=1)
                return parsed
        problems.append(f"NC report {d}: downloaded but could not parse the party table")
        return None
    problems.append("NC report: no daily report found for the last four days")
    return None


def collect_nc_raw(cfg, run_date, problems):
    """Ballots by party from the State Board's raw absentee file, which runs a day ahead of the PDF.

    Counts every ACCEPTED* ballot (mail and one-stop) by the voter's registered party. Only
    aggregates are kept; the voter-level file is never written to disk.
    """
    src = cfg["auto_sources"].get("NC_raw")
    if not src:
        return None
    try:
        _, data = fetch(src["url"], timeout=90)
        zf = zipfile.ZipFile(io.BytesIO(data))
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        counts, by_day = {}, {}
        with zf.open(name) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1")):
                if not (row.get("ballot_rtn_status") or "").startswith("ACCEPTED"):
                    continue
                try:
                    day = dt.datetime.strptime(row.get("ballot_rtn_dt") or "", "%m/%d/%Y").date()
                except ValueError:
                    continue
                if day > run_date:  # data-entry typos dated in the future
                    continue
                p = {"DEM": "D", "REP": "R"}.get(row.get("voter_party_code"), "Other")
                counts[p] = counts.get(p, 0) + 1
                by_day[day] = by_day.get(day, 0) + 1
    except Exception as e:
        problems.append(f"NC raw absentee file: {e.__class__.__name__}: {e}")
        return None
    total = sum(counts.values())
    if not total:
        problems.append("NC raw absentee file: no accepted ballots found")
        return None
    # As-of is the latest return date with a real day's volume, not a stray single record.
    as_of = max(d for d, n in by_day.items() if n >= max(25, total // 1000))
    return {"as_of": as_of.isoformat(), "total": total,
            "ballots_share": {k: round(100.0 * counts.get(k, 0) / total, 1) for k in PARTIES},
            "source": src["name"], "source_url": src["page"]}


def collect_uf(cfg, run_date, raw_dir, problems, skip_fetch=False):
    """Every state's early ballots by party from the UF Election Lab's published CSV."""
    src = cfg["auto_sources"].get("UF")
    if not src:
        return []
    cached = os.path.join(raw_dir, "early_vote_uf.csv")
    if skip_fetch:
        if not os.path.exists(cached):
            return []
        data = open(cached, "rb").read()
    else:
        try:
            _, data = fetch(src["url"])
        except Exception as e:
            problems.append(f"UF Election Lab CSV: {e.__class__.__name__}: {e}")
            return []
        os.makedirs(raw_dir, exist_ok=True)
        open(cached, "wb").write(data)
    names = {**{k: v.get("name") for k, v in cfg["research_states"].items() if isinstance(v, dict)}}
    out = []
    for r in csv.DictReader(io.StringIO(data.decode("utf-8", "replace"))):
        def n(k):
            try:
                return int(float(r.get(k) or 0))
            except ValueError:
                return 0
        st, total = r.get("state_abbv"), n("voted_all")
        d, rep, none = n("voted_dem"), n("voted_rep"), n("voted_none")
        if not st or total < src.get("min_ballots", 1000) or not (d + rep):
            continue  # no party data, or too few ballots to mean anything
        try:
            as_of = dt.datetime.strptime(r.get("last_update") or "", "%m/%d/%Y").date().isoformat()
        except ValueError:
            as_of = None
        affiliation = cfg["research_states"].get(st, {}).get("affiliation_note") if isinstance(cfg["research_states"].get(st), dict) else None
        e = {"state": st, "name": names.get(st) or r.get("state") or st, "as_of": as_of,
             "measure": f"early ballots cast (mail {n('accept_all'):,}, in person {n('inperson_all'):,})",
             "total": total, "ballots_share": {"D": round(100.0 * d / total, 1), "R": round(100.0 * rep / total, 1), "Other": round(100.0 * none / total, 1)},
             "source": f"{src['name']} (state data: {r.get('data_source') or 'state election office'})", "source_url": src["page"],
             "share_of_2022_turnout_pct": round(100.0 * total / n("turn_2022"), 1) if n("turn_2022") else None,
             "automated": True}
        if affiliation:
            e["note"] = affiliation
        out.append(e)
    return out


# ---------------------------------------------------------------- merge + derive
def derive(entry, reg):
    """Add gap-versus-registration fields. Shares are percentages of ballots."""
    b = entry.get("ballots_share") or {}
    r = entry.get("registration_share") or (reg and {k: reg.get(k) for k in PARTIES})
    if r and all(r.get(k) is not None for k in ("D", "R")):
        entry["registration_share"] = r
        entry["gap_vs_registration"] = {k: round(b[k] - r[k], 1) for k in PARTIES if b.get(k) is not None and r.get(k) is not None}
        if b.get("D") is not None and b.get("R") is not None:
            entry["ballot_margin"] = round(b["D"] - b["R"], 1)            # + = Democratic edge among early ballots
            entry["registration_margin"] = round(r["D"] - r["R"], 1)
            entry["overperformance"] = round(entry["ballot_margin"] - entry["registration_margin"], 1)  # + = D running ahead of registration
    p = entry.get("prior") or {}
    if p.get("share") and b.get("D") is not None and p["share"].get("D") is not None and p["share"].get("R") is not None:
        entry["margin_change_vs_prior"] = round((b["D"] - b["R"]) - (p["share"]["D"] - p["share"]["R"]), 1)
    if p.get("total") and entry.get("total"):
        entry["pace_vs_prior_pct"] = round(100.0 * entry["total"] / p["total"], 0)
    return entry


def margin_label(m):
    if m is None:
        return "n/a"
    return "even" if abs(m) < 0.05 else f"{'D' if m > 0 else 'R'}+{abs(m):.1f}"


def bundle_section(run_date, states, problems, research_missing):
    lines = ["", "## Early vote (party registration vs. early ballots)", "",
             "Participation signals only: party registration is not party vote, and mail ballots have skewed Democratic since 2020, so the change versus the same point in a prior cycle matters more than the raw gap. Use these numbers in the brief's Early vote section with their source links and as-of dates.", ""]
    if not states:
        lines.append("- No early vote data today.")
    for e in sorted(states, key=lambda x: -(x.get("total") or 0)):
        b, r = e.get("ballots_share", {}), e.get("registration_share") or {}
        parts = [f"**{e['state']}** ({e.get('measure', 'early ballots')}, as of {e.get('as_of') or 'unknown'})"]
        if e.get("total"):
            parts.append(f"{e['total']:,} ballots")
        parts.append("ballots " + " / ".join(f"{k} {b[k]:.1f}%" for k in PARTIES if b.get(k) is not None))
        if r:
            parts.append("registration " + " / ".join(f"{k} {r[k]:.1f}%" for k in PARTIES if r.get(k) is not None))
        if e.get("overperformance") is not None:
            parts.append(f"ballot margin {margin_label(e['ballot_margin'])} vs registration {margin_label(e['registration_margin'])} (Dems {'+' if e['overperformance'] >= 0 else ''}{e['overperformance']} vs registration)")
        if e.get("margin_change_vs_prior") is not None:
            parts.append(f"margin change vs {e['prior'].get('cycle', 'prior')} same point: {'+' if e['margin_change_vs_prior'] >= 0 else ''}{e['margin_change_vs_prior']} toward {'D' if e['margin_change_vs_prior'] >= 0 else 'R'}")
        if e.get("pace_vs_prior_pct") is not None:
            parts.append(f"volume {e['pace_vs_prior_pct']:.0f}% of {e['prior'].get('cycle', 'prior')} same point")
        if e.get("share_of_2022_turnout_pct") is not None:
            parts.append(f"equal to {e['share_of_2022_turnout_pct']:.1f}% of the state's total 2022 general-election turnout (not a same-point comparison)")
        if e.get("day_change"):
            parts.append(f"since yesterday: {e['day_change']}")
        if e.get("note"):
            parts.append(e["note"])
        lines.append("- " + "; ".join(parts) + f". Source: {e.get('source', '')} {e.get('source_url', '')}")
        if e.get("registration_source_url") and not e.get("registration_from_source"):
            lines.append(f"  - Registration baseline: {e.get('registration_source', '')}, as of {e.get('registration_as_of', '')}: {e['registration_source_url']}")
    for p in problems:
        lines.append(f"- Problem: {p}")
    if research_missing:
        lines.append("- No research file today; other states come from the UF Election Lab feed only (no same-point prior-cycle comparisons).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--skip-fetch", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    run_date = dt.date.fromisoformat(args.date) if args.date else chicago_today()
    ds = run_date.isoformat()
    season = cfg["season"]
    raw_dir = os.path.join(ROOT, "data", "raw", ds)
    problems = []

    prev = {}
    if os.path.exists(OUT_PATH):
        try:
            prev = json.load(open(OUT_PATH, encoding="utf-8"))
        except Exception:
            prev = {}
    history = prev.get("history", {})
    prev_latest = {s["state"]: s for s in prev.get("states", [])}

    if ds > season["end"]:
        print("early vote season is over; leaving docs/data/early_vote.json unchanged", file=sys.stderr)
        return 0

    states = []
    # North Carolina, automated
    nc = None if args.skip_fetch else collect_nc(cfg, run_date, raw_dir, problems)
    if nc is None and os.path.exists(os.path.join(raw_dir, "early_vote_nc.json")):
        nc = json.load(open(os.path.join(raw_dir, "early_vote_nc.json")))
    if nc:
        cfg["registration"]["NC"].update({**nc["registration_share"], "as_of": nc["as_of"], "source_url": nc["source_url"]})
        json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        entry = {"state": "NC", "name": "North Carolina", "as_of": nc["as_of"], "measure": nc["measure"], "total": nc["total"],
                 "in_person": nc["in_person"], "ballots_share": nc["ballots_share"], "registration_share": nc["registration_share"],
                 "registration_from_source": True, "turnout_pct": nc["turnout_pct"], "source": nc["source"], "source_url": nc["source_url"],
                 "prior": {"cycle": "2022", "total": nc["prior_same_point_total"]} if nc.get("prior_same_point_total") else None,
                 "automated": True}
        states.append(entry)
    # The raw ballot file is usually a day fresher than the PDF; prefer its ballot counts.
    nc_raw = None if args.skip_fetch else collect_nc_raw(cfg, run_date, problems)
    if nc_raw and (not nc or nc_raw["as_of"] > (nc.get("as_of") or "")):
        base = states.pop() if nc else {"state": "NC", "name": "North Carolina", "automated": True,
                                        "registration_share": {k: cfg["registration"]["NC"].get(k) for k in PARTIES}, "registration_from_source": True}
        if base.get("prior") and nc and nc.get("total"):
            # The 2022 same-point total belongs to the PDF's date, so keep it only as a dated note.
            base["note"] = f"PDF report through {nc['as_of']}: {nc['total']:,} ballots, {100.0 * nc['total'] / base['prior']['total']:.0f}% of the 2022 same point"
        base.update({"as_of": nc_raw["as_of"], "total": nc_raw["total"], "ballots_share": nc_raw["ballots_share"], "prior": None,
                     "measure": "accepted mail + in-person early ballots", "in_person": None,
                     "source": nc_raw["source"], "source_url": nc_raw["source_url"]})
        states.append(base)
    uf = collect_uf(cfg, run_date, raw_dir, problems, skip_fetch=args.skip_fetch)

    # Other states, from the routine's research step
    rpath = os.path.join(ROOT, "data", "early_vote", "research", f"{ds}.json")
    research_missing = not os.path.exists(rpath)
    if not research_missing:
        try:
            research = json.load(open(rpath, encoding="utf-8"))
        except Exception as e:
            research = {"states": []}
            problems.append(f"research file unreadable: {e}")
        for e in research.get("states", []):
            st = e.get("state")
            if not st or st == "NC" or not e.get("source_url") or not e.get("ballots_share"):
                problems.append(f"research entry skipped (needs state, source_url, ballots_share): {st}")
                continue
            reg = cfg["registration"].get(st)
            if reg and not e.get("registration_share"):
                e["registration_source"], e["registration_as_of"], e["registration_source_url"] = reg.get("source"), reg.get("as_of"), reg.get("source_url")
            e.setdefault("name", cfg["research_states"].get(st, {}).get("name", st))
            states.append(e)
    # UF Election Lab fills every state the research file did not cover (NC stays on its own source).
    have = {e["state"] for e in states}
    for e in uf:
        if e["state"] not in have:
            reg = cfg["registration"].get(e["state"])
            if reg:
                e["registration_source"], e["registration_as_of"], e["registration_source_url"] = reg.get("source"), reg.get("as_of"), reg.get("source_url")
            states.append(e)
    have = {e["state"] for e in states}
    if not research_missing:
        for u in research.get("unavailable", []):
            if u.get("state") not in have:  # a state UF covered is not a problem
                problems.append(f"{u.get('state')}: {u.get('reason', 'no party data found')}")

    for e in states:
        derive(e, cfg["registration"].get(e["state"]))
        # day-over-day change against the last different as-of date in history
        h = [x for x in history.get(e["state"], []) if x.get("as_of") != e.get("as_of") and x["run_date"] < ds]
        if h and e.get("ballot_margin") is not None and h[-1].get("ballot_margin") is not None:
            last = h[-1]
            dm = round(e["ballot_margin"] - last["ballot_margin"], 1)
            vol = f", {e['total'] - last['total']:+,} ballots" if e.get("total") and last.get("total") else ""
            e["day_change"] = f"D-R ballot margin {'+' if dm >= 0 else ''}{dm} pts since {last['as_of']}{vol}"
        snap = {"run_date": ds, "as_of": e.get("as_of"), "total": e.get("total"), "ballot_margin": e.get("ballot_margin"),
                "overperformance": e.get("overperformance"), "margin_change_vs_prior": e.get("margin_change_vs_prior"),
                **{f"share_{k}": e.get("ballots_share", {}).get(k) for k in PARTIES}}
        series = [x for x in history.get(e["state"], []) if x["run_date"] != ds] + [snap]
        history[e["state"]] = sorted(series, key=lambda x: x["run_date"])

    # Keep yesterday's entries for states that did not report today, marked stale
    seen = {e["state"] for e in states}
    for st, old in prev_latest.items():
        if st not in seen:
            old = dict(old)
            old["stale"] = True
            states.append(old)

    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "run_date": ds, "season": season,
           "caveat": "Party registration is not party vote. These are participation signals: who is returning early ballots compared with who is registered, and with the same point in a prior cycle when reported.",
           "states": sorted(states, key=lambda x: (x.get("stale", False), -(x.get("total") or 0))),
           "problems": problems, "history": history}
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    bundle_path = os.path.join(ROOT, "data", "bundle", f"{ds}.md")
    section = bundle_section(ds, [s for s in states if not s.get("stale")], problems, research_missing)
    if os.path.exists(bundle_path):
        txt = open(bundle_path, encoding="utf-8").read()
        txt = re.sub(r"\n## Early vote \(party registration vs\. early ballots\).*?(?=\n## |\Z)", "", txt, flags=re.S)
        open(bundle_path, "w", encoding="utf-8").write(txt.rstrip() + "\n" + section)
    else:
        problems.append(f"no bundle at {bundle_path}; run analyze.py first")
    print(f"early vote: {len([s for s in states if not s.get('stale')])} states today, {len(problems)} problems", file=sys.stderr)
    for p in problems:
        print(f"[early vote] {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail the morning run over this panel
        print(f"[early vote] unexpected error: {exc}", file=sys.stderr)
        sys.exit(0)
