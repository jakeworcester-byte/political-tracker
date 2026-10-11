#!/usr/bin/env python3
"""Turn the day's raw pulls into the tracker's data products.

Reads  data/raw/<date>/*.json (from collect.py)
Writes docs/data/latest.json      everything the public site needs
       docs/data/history.json     one compact entry per run date, for trend lines
       docs/data/races/<key>.json per-race detail incl. polls and signals
       data/bundle/<date>.md      the digest Claude reads before writing the brief
       data/bundle/<date>.json    machine-readable movers, unresolved items, Notion rows

Movement is computed numerically against the previous run (the prior latest.json
and history), so "what moved" in the brief is exact rather than recalled.

Standard library only. Usage: python3 scripts/analyze.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_URL = "https://jakeworcester-byte.github.io/political-tracker/"

RATING_SCORE = {"Solid D": -4, "Likely D": -3, "Lean D": -2, "Tilt D": -1, "Tossup": 0,
                "Tilt R": 1, "Lean R": 2, "Likely R": 3, "Solid R": 4}
SCORE_RATING = {v: k for k, v in RATING_SCORE.items()}
SHORT = {"cook": "Cook", "ddhq": "DDHQ", "econ": "Economist", "fpo": "FiftyPlusOne", "fox": "Fox", "ie": "Inside Elections",
         "rcp": "RCP", "sabato": "Sabato", "silver": "Silver", "st": "Split Ticket"}
TRACK_BAND = (-2, 2)  # Lean D through Lean R, per Jake

# Senate composition going into the 2026 cycle (seats not on the ballot are inferred from this).
SENATE_COMPOSITION = {"D": 47, "R": 53}  # Independents caucusing with Democrats counted as D
HOUSE_SEATS = 435

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return default


def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def median_score(scores):
    s = sorted(scores)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    a, b = s[n // 2 - 1], s[n // 2]
    return a if abs(a) < abs(b) else b  # ties round toward Tossup


def fmt_margin(m):
    if m is None:
        return "n/a"
    if abs(m) < 0.05:
        return "Even"
    return f"{'D' if m < 0 else 'R'}+{abs(m):.1f}"


# ----------------------------------------------------------------------------- polls

def poll_margin(poll, race, candidates):
    """Return R minus D margin for a two-party race poll, or None if parties can't be resolved."""
    answers = poll.get("answers") or []
    if len(answers) < 2:
        return None
    d_pct = r_pct = None
    cand = candidates.get(race["key"], {})
    for a in answers:
        name = (a.get("choice") or "").strip()
        party = None
        if name in cand.get("D", []):
            party = "D"
        elif name in cand.get("R", []):
            party = "R"
        if party == "D" and d_pct is None:
            d_pct = a.get("pct")
        elif party == "R" and r_pct is None:
            r_pct = a.get("pct")
    if d_pct is None or r_pct is None:
        return None
    return round(float(r_pct) - float(d_pct), 1)


def resolve_candidates(race, polls, overrides):
    """Build {D:[names], R:[names]} for a race from the incumbent and poll answer names.

    Rule: a poll choice containing the incumbent's surname takes the incumbent's party. If the race
    is a two-way contest, the other leading name takes the other major party. Open seats and
    multi-candidate fields need config/candidates.json; unresolved races are reported.
    """
    key = race["key"]
    if key in overrides:
        return {"D": list(overrides[key].get("D", [])), "R": list(overrides[key].get("R", []))}, True
    names = {}
    for p in polls:
        for a in p.get("answers") or []:
            n = (a.get("choice") or "").strip()
            if not n or n.lower() in ("undecided", "other", "someone else", "refused", "none", "not sure"):
                continue
            names[n] = max(names.get(n, 0), float(a.get("pct") or 0))
    if not names:
        return {"D": [], "R": []}, False
    inc_party = race.get("incumbent_party")
    surname = race["incumbent"].split()[-1].lower() if race.get("incumbent") else None
    out = {"D": [], "R": []}
    if surname and inc_party in ("D", "R") and not race.get("open_seat"):
        inc_names = [n for n in names if surname in n.lower()]
        if inc_names:
            out[inc_party].extend(inc_names)
            other = "D" if inc_party == "R" else "R"
            rest = sorted((n for n in names if n not in inc_names), key=lambda n: -names[n])
            if rest and names[rest[0]] >= 20:
                out[other].append(rest[0])
            if out["D"] and out["R"]:
                return out, True
    return out, False


def polling_summary(race, polls, candidates, run_date):
    usable = []
    for p in polls:
        if p.get("internal"):
            continue
        m = poll_margin(p, race, candidates)
        if m is None:
            continue
        usable.append((p, m))
    if not usable:
        return None
    usable.sort(key=lambda x: x[0].get("end_date") or "", reverse=True)
    latest_end = usable[0][0].get("end_date")
    cutoff21 = (dt.date.fromisoformat(latest_end) - dt.timedelta(days=21)).isoformat()
    window = [x for x in usable if (x[0].get("end_date") or "") >= cutoff21]
    window_days = 21
    if len(window) < 2:
        cutoff45 = (dt.date.fromisoformat(latest_end) - dt.timedelta(days=45)).isoformat()
        window = [x for x in usable if (x[0].get("end_date") or "") >= cutoff45]
        window_days = 45
    margins = [m for _, m in window]
    avg = round(statistics.mean(margins), 1) if margins else None
    lp, lm = usable[0]
    return {
        "average_margin": avg, "average_label": fmt_margin(avg), "n_polls": len(window), "window_days": window_days,
        "latest": {
            "pollster": lp.get("pollster"), "sponsors": lp.get("sponsors") or [], "partisan": lp.get("partisan"),
            "start_date": lp.get("start_date"), "end_date": lp.get("end_date"), "sample_size": lp.get("sample_size"),
            "population": lp.get("population"), "margin": lm, "margin_label": fmt_margin(lm),
            "answers": lp.get("answers"), "url": lp.get("url"),
        },
        "polls": [{
            "pollster": p.get("pollster"), "end_date": p.get("end_date"), "margin": m, "n": p.get("sample_size"),
            "population": p.get("population"), "partisan": p.get("partisan"), "url": p.get("url"),
            "answers": p.get("answers"),
        } for p, m in usable[:12]],
    }


def national_polls(votehub, run_date):
    polls = votehub.get("polls", []) if votehub else []
    today = dt.date.fromisoformat(run_date)

    def avg(kind, days, f):
        cutoff = (today - dt.timedelta(days=days)).isoformat()
        rows = [p for p in polls if p.get("poll_type") == kind and (p.get("end_date") or "") >= cutoff and not p.get("internal")]
        vals = [v for v in (f(p) for p in rows) if v is not None]
        return rows, vals

    out = {}
    def gb_margin(p):
        d = r = None
        for a in p.get("answers") or []:
            c = (a.get("choice") or "").lower()
            if c.startswith("dem"):
                d = a.get("pct")
            elif c.startswith("rep") or c.startswith("gop"):
                r = a.get("pct")
        return round(float(r) - float(d), 1) if d is not None and r is not None else None

    rows, vals = avg("generic-ballot", 21, gb_margin)
    if len(vals) < 3:
        rows, vals = avg("generic-ballot", 45, gb_margin)
    out["generic_ballot"] = {
        "average_margin": round(statistics.mean(vals), 1) if vals else None,
        "average_label": fmt_margin(round(statistics.mean(vals), 1)) if vals else "n/a",
        "n_polls": len(vals),
        "polls": [{"pollster": p.get("pollster"), "end_date": p.get("end_date"), "margin": gb_margin(p),
                   "n": p.get("sample_size"), "population": p.get("population"), "sponsors": p.get("sponsors"),
                   "partisan": p.get("partisan"), "url": p.get("url")} for p in sorted(rows, key=lambda p: p.get("end_date") or "", reverse=True)[:10]],
    }

    def net(p):
        a = d = None
        for x in p.get("answers") or []:
            c = (x.get("choice") or "").lower()
            if c.startswith("approve"):
                a = x.get("pct")
            elif c.startswith("disapprove"):
                d = x.get("pct")
        return (float(a), float(d)) if a is not None and d is not None else None

    rows, vals = avg("approval", 14, net)
    if len(vals) < 3:
        rows, vals = avg("approval", 30, net)
    out["approval"] = {
        "approve": round(statistics.mean(v[0] for v in vals), 1) if vals else None,
        "disapprove": round(statistics.mean(v[1] for v in vals), 1) if vals else None,
        "net": round(statistics.mean(v[0] - v[1] for v in vals), 1) if vals else None,
        "n_polls": len(vals),
        "polls": [{"pollster": p.get("pollster"), "end_date": p.get("end_date"), "approve": net(p)[0] if net(p) else None,
                   "disapprove": net(p)[1] if net(p) else None, "n": p.get("sample_size"), "population": p.get("population"),
                   "url": p.get("url")} for p in sorted(rows, key=lambda p: p.get("end_date") or "", reverse=True)[:10]],
    }
    return out


# ----------------------------------------------------------------------------- markets

def market_summary(poly, kalshi):
    out = {"senate": None, "house": None, "balance": None, "kalshi": []}
    for e in (poly or {}).get("events", []):
        if e["slug"].startswith("which-party-will-win-the-senate"):
            out["senate"] = _control_from_event(e)
        elif e["slug"].startswith("which-party-will-win-the-house"):
            out["house"] = _control_from_event(e)
        elif e["slug"].startswith("balance-of-power"):
            combos = {}
            for m in e["markets"]:
                q = m["question"]
                mm = re.search(r":\s*(.*)$", q)
                combos[mm.group(1).strip() if mm else q] = round(m["prices"][0] * 100, 1)
            out["balance"] = {"combos": combos, "url": e["url"], "source": "Polymarket", "volume": e["volume"]}
    for m in (kalshi or {}).get("markets", []):
        out["kalshi"].append(m)
    return out


def _control_from_event(e):
    d = r = None
    for m in e["markets"]:
        q = (m["question"] or "").lower()
        if "democratic" in q and m["prices"]:
            d = round(m["prices"][0] * 100, 1)
        elif "republican" in q and m["prices"]:
            r = round(m["prices"][0] * 100, 1)
    return {"D": d, "R": r, "url": e["url"], "source": "Polymarket", "volume": e["volume"]}


# ----------------------------------------------------------------------------- signals

def race_matchers(race, candidates):
    """Return lowercase phrases that identify a race in a headline."""
    phrases = []
    st = race["state"].lower()
    if race["chamber"] == "Senate":
        phrases.append(f"{st} senate")
        phrases.append(f"senate race in {st}")
        phrases.append(f"{st}'s senate")
    else:
        d = race["district"]
        phrases.append(f"{race['state_abbr'].lower()}-{d}")
        phrases.append(f"{race['state_abbr'].lower()}-{d.lstrip('0')}")
        if d != "AL":
            n = int(d)
            ordinal = f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
            phrases.append(f"{st}'s {ordinal}")
            phrases.append(f"{st} {ordinal}")
            # "9th district" alone is ambiguous across states; require the state name too (handled in match_race).
            phrases.append(f"@{ordinal} district")
            phrases.append(f"@{ordinal} congressional district")
        else:
            phrases.append(f"@at-large")
    names = []
    if race.get("incumbent") and not race.get("open_seat"):
        names.append(race["incumbent"].split()[-1].lower())
    for party in ("D", "R"):
        for n in candidates.get(race["key"], {}).get(party, []):
            names.append(n.split()[-1].lower())
    return phrases, [n for n in names if len(n) > 3]


def build_signals(feeds, gnews, races, candidates, run_date):
    """Attach feed items and Google News items to races (or National). Returns list of signals."""
    signals = []
    tracked = [r for r in races if r["tracked"]]
    matchers = {r["key"]: race_matchers(r, candidates) for r in tracked}

    def match_race(text):
        t = text.lower()
        hits = []
        for key, (phrases, names) in matchers.items():
            r = next(x for x in tracked if x["key"] == key)
            plain = [p for p in phrases if not p.startswith("@")]
            needs_state = [p[1:] for p in phrases if p.startswith("@")]
            if any(p in t for p in plain) or (r["state"].lower() in t and any(p in t for p in needs_state)):
                hits.append(key)
            elif names and r["state"].lower() in t and any(re.search(r"\b" + re.escape(n) + r"\b", t) for n in names):
                hits.append(key)
        return hits

    for f in (feeds or {}).get("feeds", []):
        for it in f["items"]:
            text = f"{it['title']} {it.get('summary', '')}"
            keys = match_race(text) or ["National"]
            kind = {"reporting": "reporting", "punditry": "punditry", "podcast": "podcast"}[f["kind"]]
            signals.append({
                "date": (it.get("published") or run_date)[:10], "race": keys[0], "races": keys, "type": kind,
                "source": f["name"], "lean": f["lean"], "title": it["title"], "url": it["link"],
                "summary": it.get("summary", "")[:300], "relevant": it.get("relevant", True),
            })
    for key, blob in (gnews or {}).get("races", {}).items():
        for it in blob["items"]:
            # Google's relevance ranking is fuzzy ("Ohio 9th" can return "Illinois 9th"); keep only titles that name this race.
            if key in matchers and key not in match_race(it["title"]):
                continue
            signals.append({
                "date": (it.get("published") or run_date)[:10], "race": key, "races": [key], "type": "reporting",
                "source": it.get("outlet") or "Google News", "lean": "unrated", "title": it["title"], "url": it["link"],
                "summary": "", "relevant": True, "via": "Google News",
            })
    for key, blob in (gnews or {}).get("national", {}).items():
        for it in blob["items"]:
            signals.append({
                "date": (it.get("published") or run_date)[:10], "race": "National", "races": ["National"], "type": "reporting",
                "source": it.get("outlet") or "Google News", "lean": "unrated", "title": it["title"], "url": it["link"],
                "summary": "", "relevant": True, "via": "Google News",
            })
    # de-duplicate by URL
    seen, out = set(), []
    for s in signals:
        k = s["url"] or s["title"]
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    out.sort(key=lambda s: s["date"], reverse=True)
    return out


# ----------------------------------------------------------------------------- main assembly

def chamber_overview(races, chamber):
    rows = [r for r in races if r["chamber"] == chamber]
    buckets = {k: 0 for k in RATING_SCORE}
    for r in rows:
        buckets[r["consensus"]] += 1
    up_by_party = {"D": sum(1 for r in rows if r["incumbent_party"] == "D"), "R": sum(1 for r in rows if r["incumbent_party"] == "R")}
    out = {"races_rated": len(rows), "consensus_buckets": buckets, "seats_up_by_incumbent_party": up_by_party,
           "tracked": sum(1 for r in rows if r["tracked"])}
    if chamber == "Senate":
        not_up = {p: SENATE_COMPOSITION[p] - up_by_party.get(p, 0) for p in ("D", "R")}
        safe_d = sum(1 for r in rows if r["consensus_score"] <= -3)
        safe_r = sum(1 for r in rows if r["consensus_score"] >= 3)
        comp = sum(1 for r in rows if -2 <= r["consensus_score"] <= 2)
        out["not_up"] = not_up
        out["projection_by_consensus"] = {"D_solid_or_likely": not_up["D"] + safe_d, "R_solid_or_likely": not_up["R"] + safe_r,
                                          "competitive": comp, "majority": 51}
    return out


def house_overview_from_forecasters(ratings_house):
    overall = (ratings_house or {}).get("overall", {})
    return {k: v for k, v in overall.items()}


def detect_changes(races, prev_latest, markets, national, prev_history_entry):
    """Compare today's state to the previous run. Returns list of movement signals."""
    movers = []
    prev_races = {r["key"]: r for r in (prev_latest or {}).get("races", [])}
    for r in races:
        p = prev_races.get(r["key"])
        if not p:
            continue
        if p.get("consensus") != r["consensus"]:
            movers.append({"type": "rating change", "race": r["key"], "name": r["name"],
                           "text": f"Consensus moved {p['consensus']} -> {r['consensus']}",
                           "delta": r["consensus_score"] - p["consensus_score"],
                           "url": r["ratings_source_url"], "source": "Forecaster consensus (Wikipedia compilation)"})
        for abbr, v in r["ratings"].items():
            pv = (p.get("ratings") or {}).get(abbr)
            if pv and pv.get("rating") != v["rating"]:
                movers.append({"type": "rating change", "race": r["key"], "name": r["name"],
                               "text": f"{v['name']}: {pv['rating']} -> {v['rating']} (as of {v['as_of']})",
                               "delta": RATING_SCORE[v["rating"]] - RATING_SCORE[pv["rating"]],
                               "url": r["ratings_source_url"], "source": v["name"]})
        pm = (p.get("poll") or {}).get("average_margin")
        cm = (r.get("poll") or {}).get("average_margin")
        if pm is not None and cm is not None and abs(cm - pm) >= 2.0:
            movers.append({"type": "poll shift", "race": r["key"], "name": r["name"],
                           "text": f"Polling average {fmt_margin(pm)} -> {fmt_margin(cm)} ({r['poll']['n_polls']} polls, {r['poll']['window_days']}d)",
                           "delta": round(cm - pm, 1), "url": SITE_URL + f"#race/{r['key']}", "source": "VoteHub polls"})
    prev_m = (prev_latest or {}).get("national", {}).get("markets", {}) if prev_latest else {}
    for ch in ("senate", "house"):
        cur, prev = (markets.get(ch) or {}), (prev_m.get(ch) or {})
        if cur.get("D") is not None and prev.get("D") is not None and abs(cur["D"] - prev["D"]) >= 5:
            movers.append({"type": "market move", "race": "National", "name": f"{ch.title()} control",
                           "text": f"Polymarket Democratic {ch.title()} control {prev['D']}% -> {cur['D']}%",
                           "delta": round(cur["D"] - prev["D"], 1), "url": cur.get("url"), "source": "Polymarket"})
    prev_gb = ((prev_latest or {}).get("national", {}).get("generic_ballot") or {}).get("average_margin") if prev_latest else None
    cur_gb = national["generic_ballot"]["average_margin"]
    if prev_gb is not None and cur_gb is not None and abs(cur_gb - prev_gb) >= 1.5:
        movers.append({"type": "poll shift", "race": "National", "name": "Generic ballot",
                       "text": f"Generic ballot average {fmt_margin(prev_gb)} -> {fmt_margin(cur_gb)}",
                       "delta": round(cur_gb - prev_gb, 1), "url": SITE_URL + "#national", "source": "VoteHub polls"})
    prev_ap = ((prev_latest or {}).get("national", {}).get("approval") or {}).get("net") if prev_latest else None
    cur_ap = national["approval"]["net"]
    if prev_ap is not None and cur_ap is not None and abs(cur_ap - prev_ap) >= 1.5:
        movers.append({"type": "poll shift", "race": "National", "name": "Presidential approval",
                       "text": f"Net approval {prev_ap:+.1f} -> {cur_ap:+.1f}", "delta": round(cur_ap - prev_ap, 1),
                       "url": SITE_URL + "#national", "source": "VoteHub polls"})
    return movers


def trend_for(key, history, cur_score, cur_margin):
    """Compare against the entry roughly 7 days ago."""
    if not history:
        return "Stable"
    older = [h for h in history if h["date"] <= (dt.date.fromisoformat(history[-1]["date"]) - dt.timedelta(days=6)).isoformat()]
    base = older[-1] if older else history[0]
    e = (base.get("races") or {}).get(key)
    if not e:
        return "Stable"
    ds = (cur_score or 0) - (e.get("c") or 0)
    dm = None
    if cur_margin is not None and e.get("p") is not None:
        dm = cur_margin - e["p"]
    if ds <= -1 or (dm is not None and dm <= -2.0):
        return "Toward D"
    if ds >= 1 or (dm is not None and dm >= 2.0):
        return "Toward R"
    return "Stable"


def write_bundle(run_date, races, national, markets, movers, signals, coverage, unresolved, senate_ov, house_ov, house_forecasters, path_md, path_json):
    tracked = [r for r in races if r["tracked"]]
    lines = []
    L = lines.append
    L(f"# Tracker digest for {run_date}")
    L("")
    L("This file is generated by scripts/analyze.py. Everything below is sourced; keep URLs when you cite.")
    L("")
    L("## National")
    gb, ap = national["generic_ballot"], national["approval"]
    L(f"- Generic ballot average: {gb['average_label']} ({gb['n_polls']} polls). Latest: " + "; ".join(
        f"{p['pollster']} {fmt_margin(p['margin'])} ({p['end_date']})" for p in gb["polls"][:3]))
    L(f"- Presidential approval average: {ap['approve']}% approve / {ap['disapprove']}% disapprove, net {ap['net']:+.1f} ({ap['n_polls']} polls)" if ap["net"] is not None else "- Presidential approval: no recent polls")
    for ch in ("senate", "house"):
        m = markets.get(ch)
        if m:
            L(f"- Polymarket {ch.title()} control: D {m['D']}% / R {m['R']}% ({m['url']})")
    if markets.get("balance"):
        L("- Polymarket balance of power: " + ", ".join(f"{k} {v}%" for k, v in markets["balance"]["combos"].items()))
    L("")
    L("## Chamber overview (forecaster consensus)")
    L(f"- Senate: {senate_ov['projection_by_consensus']['D_solid_or_likely']} D solid/likely (incl. {senate_ov['not_up']['D']} not up), "
      f"{senate_ov['projection_by_consensus']['R_solid_or_likely']} R solid/likely (incl. {senate_ov['not_up']['R']} not up), "
      f"{senate_ov['projection_by_consensus']['competitive']} competitive (Lean/Tilt/Tossup). Majority is 51.")
    L("- House by forecaster (D / R / tossups): " + "; ".join(f"{v['name']} {v['D']}/{v['R']}/{v['tossups']}" for v in house_forecasters.values()))
    L(f"- House consensus buckets: " + ", ".join(f"{k} {v}" for k, v in house_ov["consensus_buckets"].items() if v))
    L("")
    L(f"## What moved since the last run ({len(movers)} items)")
    if not movers:
        L("- Nothing crossed a threshold. If true, say so in one line.")
    for m in movers:
        L(f"- [{m['type']}] {m['name']}: {m['text']} ({m['source']}; {m['url']})")
    L("")
    L(f"## Tracked races ({len(tracked)}): consensus | forecaster ratings | polling | market | trend")
    for r in sorted(tracked, key=lambda r: (r["chamber"] != "Senate", r["consensus_score"], r["key"])):
        rt = ", ".join(f"{SHORT.get(k, k)} {v['rating']}" for k, v in r["ratings"].items())
        poll = r.get("poll")
        ps = f"{poll['average_label']} avg of {poll['n_polls']} ({poll['window_days']}d); latest {poll['latest']['pollster']} {poll['latest']['margin_label']} {poll['latest']['end_date']}" if poll else "no resolvable polls"
        flag = " DISAGREEMENT" if r["disagreement"] else ""
        L(f"- {r['key']} {r['name']} | inc {r['incumbent']} ({r['incumbent_party']}{', open' if r['open_seat'] else ''}) | {r['consensus']}{flag} | {rt} | polls: {ps} | trend {r['trend']}")
    L("")
    if unresolved:
        L("## Races with polls but unresolved candidate parties (fill config/candidates.json)")
        for u in unresolved:
            L(f"- {u['key']}: names seen in polls: {', '.join(u['names'])}")
        L("")
    L("## Recent signals by lean (headline | source | date | url)")
    by_lean = {}
    for s in signals:
        if s["race"] == "National" and not s.get("relevant", True):
            continue
        by_lean.setdefault(s["lean"], []).append(s)
    for lean in ("left", "center-left", "center", "center-right", "right", "nonpartisan data", "unrated"):
        items = by_lean.get(lean, [])
        if not items:
            continue
        L(f"### {lean} ({len(items)})")
        for s in items[:25]:
            L(f"- [{s['type']}] {s['title']} | {s['source']} | {s['date']} | {s['url']} | race {s['race']}")
        L("")
    L("## Coverage")
    L(f"- {coverage['succeeded']}/{coverage['attempted']} sources ok. Failed: {', '.join(coverage['failed_sources']) or 'none'}")
    L("")
    os.makedirs(os.path.dirname(path_md), exist_ok=True)
    open(path_md, "w", encoding="utf-8").write("\n".join(lines))

    notion_rows = []
    for r in tracked:
        poll = r.get("poll")
        notion_rows.append({
            "Race": r["name"], "Key": r["key"], "Chamber": r["chamber"], "State": r["state"], "District": r["district"] or "",
            "Incumbent": r["incumbent"], "Incumbent party": "Open" if r["open_seat"] else (r["incumbent_party"] or "I"),
            "Consensus rating": r["consensus"], "Tracked": True, "Forecaster disagreement": r["disagreement"],
            "Ratings by forecaster": "; ".join(f"{v['name']}: {v['rating']} ({v['as_of']})" for v in r["ratings"].values()),
            "Polling average": f"{poll['average_label']} ({poll['n_polls']} polls, {poll['window_days']}d)" if poll else "no resolvable polls",
            "Market odds": "", "Trend": r["trend"], "Site link": SITE_URL + f"#race/{r['key']}",
        })
    dump({"run_date": run_date, "movers": movers, "unresolved_candidates": unresolved, "notion_race_rows": notion_rows,
          "coverage": {k: coverage[k] for k in ("attempted", "succeeded", "failed", "failed_sources")},
          "national": national, "markets": markets, "signal_count": len(signals)}, path_json)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    run_date = args.date or now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    raw = os.path.join(ROOT, "data", "raw", run_date)
    if not os.path.isdir(raw):
        print(f"no raw data for {run_date}; run collect.py first", file=sys.stderr)
        return 2

    ratings_s = load(os.path.join(raw, "ratings_senate.json"))
    ratings_h = load(os.path.join(raw, "ratings_house.json"))
    votehub = load(os.path.join(raw, "votehub.json"), {"polls": []})
    poly = load(os.path.join(raw, "polymarket.json"), {})
    kalshi = load(os.path.join(raw, "kalshi.json"), {})
    feeds = load(os.path.join(raw, "feeds.json"), {})
    gnews = load(os.path.join(raw, "google_news.json"), {})
    coverage = load(os.path.join(raw, "coverage.json"), {"attempted": 0, "succeeded": 0, "failed": 0, "failed_sources": []})
    overrides = load(os.path.join(ROOT, "config", "candidates.json"), {})
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}

    prev_latest = load(os.path.join(ROOT, "docs", "data", "latest.json"))
    if prev_latest and prev_latest.get("run_date") == run_date:
        prev_latest = prev_latest.get("_previous_snapshot") or None  # same-day rerun: compare to the run before
    history = load(os.path.join(ROOT, "docs", "data", "history.json"), [])
    history = [h for h in history if h["date"] != run_date]

    if not ratings_s or not ratings_h:
        print("ratings missing; cannot build the tracker", file=sys.stderr)
        return 2

    # Build race records
    races = []
    polls = votehub.get("polls", [])
    senate_polls = {}
    for p in polls:
        if p.get("poll_type") == "us-senator":
            st = (p.get("subject") or "").replace("2026", "").strip()
            senate_polls.setdefault(st, []).append(p)
    house_polls = {}
    for p in polls:
        if p.get("poll_type") == "us-representative" and p.get("seat_name"):
            house_polls.setdefault(p["seat_name"], []).append(p)

    candidates, unresolved = {}, []
    for data, chamber in ((ratings_s, "Senate"), (ratings_h, "House")):
        for r in data["races"]:
            scores = [RATING_SCORE[v["rating"]] for v in r["ratings"].values()]
            cs = median_score(scores)
            rec = dict(r)
            rec["incumbent_note"] = ", ".join(re.findall(r"\(([^)]*)\)", r.get("incumbent", "")))
            rec["incumbent"] = re.sub(r"\s*\([^)]*\)\s*", " ", r.get("incumbent", "")).strip()
            rec["consensus_score"] = cs
            rec["consensus"] = SCORE_RATING[cs]
            rec["disagreement"] = (max(scores) - min(scores)) >= 4  # e.g. Tossup vs Solid, or Lean D vs Lean R
            rec["tracked"] = TRACK_BAND[0] <= cs <= TRACK_BAND[1]
            rec["ratings_source_url"] = data["source_url"]
            rec["flip_votes"] = sum(1 for v in r["ratings"].values() if v.get("flip"))
            if chamber == "Senate":
                rp = senate_polls.get(r["state"], [])
                if r["special"]:
                    rp = [p for p in rp if "special" in (p.get("subject") or "").lower()] or rp
            else:
                rp = house_polls.get(f"{r['state_abbr']}-{r['district'] if r['district'] != 'AL' else '01'}", [])
            cand, resolved = resolve_candidates(rec, rp, overrides)
            candidates[rec["key"]] = cand
            rec["candidates"] = cand
            rec["poll"] = polling_summary(rec, rp, candidates, run_date) if rp else None
            rec["raw_poll_count"] = len(rp)
            if rp and not resolved and rec["tracked"]:
                names = sorted({(a.get("choice") or "").strip() for p in rp for a in (p.get("answers") or [])} - {"", "Undecided", "Other"})
                unresolved.append({"key": rec["key"], "names": names[:8]})
            races.append(rec)

    national = national_polls(votehub, run_date)
    markets = market_summary(poly, kalshi)
    national["markets"] = markets

    # Trends need history; compute after loading.
    hist_sorted = sorted(history, key=lambda h: h["date"])
    for r in races:
        r["trend"] = trend_for(r["key"], hist_sorted, r["consensus_score"], (r.get("poll") or {}).get("average_margin"))

    movers = detect_changes(races, prev_latest, markets, national, hist_sorted[-1] if hist_sorted else None)
    signals = build_signals(feeds, gnews, races, candidates, run_date)
    for r in races:
        r["signals"] = [s for s in signals if r["key"] in s["races"]][:20]
    senate_ov = chamber_overview(races, "Senate")
    house_ov = chamber_overview(races, "House")
    house_fc = house_overview_from_forecasters(ratings_h)

    # History entry (compact)
    entry = {
        "date": run_date,
        "markets": {k: (v.get("D") if isinstance(v, dict) else None) for k, v in markets.items() if k in ("senate", "house")},
        "generic_ballot": national["generic_ballot"]["average_margin"],
        "approval_net": national["approval"]["net"],
        "house_forecasters": {k: {"D": v["D"], "R": v["R"], "t": v["tossups"]} for k, v in house_fc.items()},
        "senate_competitive": senate_ov["projection_by_consensus"]["competitive"],
        "races": {r["key"]: {"c": r["consensus_score"], "p": (r.get("poll") or {}).get("average_margin")} for r in races},
    }
    history = hist_sorted + [entry]
    dump(history, os.path.join(ROOT, "docs", "data", "history.json"))

    # Site payload
    latest_prev_brief = (prev_latest or {}).get("brief") if prev_latest else None
    site_races = []
    for r in races:
        sr = {k: r[k] for k in ("key", "name", "chamber", "state", "state_abbr", "district", "special", "incumbent", "incumbent_party",
                                 "open_seat", "pvi", "last_result", "consensus", "consensus_score", "disagreement", "tracked",
                                 "ratings", "trend", "candidates", "flip_votes", "raw_poll_count")}
        sr["poll"] = None
        if r.get("poll"):
            sr["poll"] = {k: r["poll"][k] for k in ("average_margin", "average_label", "n_polls", "window_days", "latest")}
        sr["signal_count"] = len(r["signals"])
        site_races.append(sr)
        dump({**r, "site_url": SITE_URL + f"#race/{r['key']}", "history": [
            {"date": h["date"], **(h["races"].get(r["key"]) or {})} for h in history if r["key"] in h.get("races", {})]},
             os.path.join(ROOT, "docs", "data", "races", f"{r['key']}.json"))

    latest = {
        "generated_at": now.isoformat(), "run_date": run_date, "site_title": "Jake's Political Tracker",
        "disclaimer": "Not a forecast. A daily synthesis of what credible forecasters, public polls, prediction markets, and reporting say about the 2026 midterms, with movement over time.",
        "sources": {"ratings": [ratings_s["source_url"], ratings_h["source_url"]], "polls": "https://votehub.com",
                    "markets": "https://polymarket.com", "forecasters": [f["name"] for f in ratings_s["forecasters"]]},
        "forecaster_dates": {"senate": {f["abbr"]: f["as_of"] for f in ratings_s["forecasters"]},
                             "house": {f["abbr"]: f["as_of"] for f in ratings_h["forecasters"]}},
        "national": national,
        "overview": {"senate": senate_ov, "house": house_ov, "house_forecasters": house_fc,
                     "senate_composition": SENATE_COMPOSITION, "house_seats": HOUSE_SEATS},
        "movers": movers,
        "races": site_races,
        "national_signals": [s for s in signals if s["race"] == "National"][:60],
        "coverage": {k: coverage.get(k) for k in ("attempted", "succeeded", "failed", "failed_sources")},
        "brief": latest_prev_brief if (latest_prev_brief and latest_prev_brief.get("date") == run_date) else None,
        "_previous_snapshot": None,
    }
    # Keep a slim copy of the previous run so a same-day rerun still diffs against yesterday.
    if prev_latest:
        latest["_previous_snapshot"] = {"run_date": prev_latest.get("run_date"), "national": prev_latest.get("national"),
                                        "races": [{k: r.get(k) for k in ("key", "consensus", "consensus_score", "ratings", "poll")} for r in prev_latest.get("races", [])]}
    dump(latest, os.path.join(ROOT, "docs", "data", "latest.json"))

    write_bundle(run_date, races, national, markets, movers, signals, coverage, unresolved, senate_ov, house_ov, house_fc,
                 os.path.join(ROOT, "data", "bundle", f"{run_date}.md"), os.path.join(ROOT, "data", "bundle", f"{run_date}.json"))
    tracked_n = sum(1 for r in races if r["tracked"])
    print(f"analyzed {len(races)} races ({tracked_n} tracked), {len(movers)} movers, {len(signals)} signals, "
          f"{len(unresolved)} unresolved candidate sets; wrote docs/data and data/bundle/{run_date}.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
