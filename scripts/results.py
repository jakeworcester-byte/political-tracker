#!/usr/bin/env python3
"""Post-election results tracker (stdlib only).

From config/postelection.json's post_election_from date, the routine runs this after collect.py
and analyze.py. It:
  * reads the House and Senate results tables on Wikipedia's 2026 election articles (editors there
    follow AP and network calls; a called race shows a "Y" winner mark and/or an outcome line such
    as "Incumbent re-elected." or "Democratic gain.")
  * compares them with the previous results snapshot (data/results/) to list races called since
    the last update
  * joins the frozen pre-election forecaster ratings (last raw snapshot before election day) to
    separate competitive uncalled races from safe seats whose table row simply hasn't been updated
  * searches Google News for every competitive uncalled race and for national leads on chamber
    calls, recounts, certification, legal and federal action, and exit polls
  * writes data/results/<date>-<edition>.json, docs/data/results.json (the site's Results tab),
    data/raw/<date>/results_news_<edition>.json, and appends a "Results" section to the digest

Nothing here calls a race. A race counts as called only when the Wikipedia table marks it, and the
brief always attributes calls. Headlines are leads for Claude to verify, not calls.

Usage: python3 scripts/results.py [--date YYYY-MM-DD] [--edition am|pm] [--skip-news]
Never exits non-zero for a source problem; problems are listed in the digest.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402  (fetch, TableParser, parse_feed, STATES, CONFIG)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config", "postelection.json"), encoding="utf-8"))
SITE_URL = "https://jakeworcester-byte.github.io/political-tracker/"
SENATE_COMPOSITION = {"D": 47, "R": 53}  # before the election; independents caucusing with Democrats counted as D
HOUSE_SEATS = 435
RATING_SCORE = {"Solid D": -4, "Likely D": -3, "Lean D": -2, "Tilt D": -1, "Tossup": 0,
                "Tilt R": 1, "Lean R": 2, "Likely R": 3, "Solid R": 4}

OUTCOME_RE = re.compile(r"re-?elected|\bnew (?:member|senator|representative) elected\b|\b(?:democratic|republican|independent)\S* (?:gain|hold)\b"
                        r"|\blost re-?election\b", re.I)
PENDING_RE = re.compile(r"to be elected|running|renominated|advanced to|retiring|nominated|seeking", re.I)
CALL_HL = re.compile(r"\b(wins?|won|defeats?|defeated|re-?elected|projected winner|declared (?:the )?winner|concedes?|conceded|flips?|holds? (?:on|seat)|victory|victorious|ousts?|unseats?)\b", re.I)
UNCERTAIN_HL = re.compile(r"too close to call|uncalled|still (?:being )?count|recount|runoff|ranked[- ]choice|outstanding ballots|provisional|cur(?:e|ing) ballots|not yet called|tight race|razor|narrow lead|leads? by", re.I)
LEGAL_HL = re.compile(r"lawsuit|sues?|sued|court|judge|ruling|injunction|appeal|Justice Department|DOJ|subpoena|certif", re.I)


# --------------------------------------------------------------------------- helpers

def chicago_now():
    now = dt.datetime.now(dt.timezone.utc)
    # America/Chicago: CDT (UTC-5) until the first Sunday in November, CST (UTC-6) after. Good enough
    # for choosing a date and edition; zoneinfo may be missing on minimal VMs.
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Chicago"))
    except Exception:  # noqa: BLE001
        return now.astimezone(dt.timezone(dt.timedelta(hours=-6)))


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def expand(table):
    """Expand rowspan/colspan into a rectangular grid of cell texts."""
    grid, pending = [], {}  # pending[(row, col)] = text carried down by rowspan
    for ri, row in enumerate(table["rows"]):
        out, ci = [], 0
        cells = list(row)
        while cells or (ri, ci) in pending:
            if (ri, ci) in pending:
                out.append(pending.pop((ri, ci)))
                ci += 1
                continue
            c = cells.pop(0)
            for k in range(c.get("colspan", 1)):
                out.append(c["text"])
                for r in range(1, c.get("rowspan", 1)):
                    pending[(ri + r, ci)] = c["text"]
                ci += 1
        grid.append(out)
    return grid


def party_code(name):
    n = (name or "").strip().lower()
    if n.startswith("democratic") or n.startswith("dfl") or n == "democrat":
        return "D"
    if n.startswith("republican"):
        return "R"
    if n.startswith("independent"):
        return "I"
    return name.strip()[:12] if name else "?"


def parse_candidates(text):
    """'▌Y Clay Fuller (Republican) 55.9% ▌Shawn Harris (Democratic) 44.1%' -> list of dicts."""
    out = []
    for chunk in [c.strip() for c in (text or "").split("▌") if c.strip()]:
        winner = False
        if re.match(r"^Y\s", chunk):
            winner, chunk = True, chunk[2:].strip()
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*(?:([\d.]+)\s*%)?", chunk)
        if not m:
            continue
        name, party, pct = m.group(1).strip(), m.group(2).strip(), m.group(3)
        out.append({"name": name, "party": party_code(party), "party_label": party,
                    "pct": float(pct) if pct else None, "winner": winner})
    return out


def find_col(header, *needles):
    for i, h in enumerate(header):
        hl = h.lower()
        if any(n in hl for n in needles):
            return i
    return None


def house_key(location):
    m = re.match(r"^(.*?)\s+(\d+|at-large)\b", location.strip(), re.I)
    if not m:
        return None, None
    state, dist = m.group(1).strip(), m.group(2).lower()
    abbr = collect.STATES.get(state)
    if not abbr:
        return None, None
    d = "AL" if dist == "at-large" else f"{int(dist):02d}"
    return f"HOU-{abbr}-{d}", f"{abbr}-{d}"


def senate_key(constituency, special):
    state = re.sub(r"\s*\(.*\)$", "", constituency).strip()
    abbr = collect.STATES.get(state)
    if not abbr:
        return None, None
    key = f"SEN-{abbr}" + ("-S" if special else "")
    return key, f"{state} Senate" + (" (special)" if special else "")


def race_status(status_text, cands):
    called = any(c["winner"] for c in cands)
    outcome = bool(OUTCOME_RE.search(status_text or "")) and not re.search(r"to be elected", status_text or "", re.I)
    winner = next((c for c in cands if c["winner"]), None)
    if not winner and outcome:
        with_pct = [c for c in cands if c["pct"] is not None]
        if len(with_pct) >= 1:
            winner = max(with_pct, key=lambda c: c["pct"])
    is_called = called or (outcome and winner is not None)
    pcts = sorted([c for c in cands if c["pct"] is not None], key=lambda c: -c["pct"])
    margin = round(pcts[0]["pct"] - pcts[1]["pct"], 1) if len(pcts) >= 2 else None
    leader = pcts[0] if pcts else None
    return {"called": bool(is_called), "winner": winner, "leader": leader, "margin_pts": margin,
            "has_results": bool(pcts)}


# --------------------------------------------------------------------------- Wikipedia tables

def parse_house(html):
    p = collect.TableParser()
    p.feed(html)
    races = {}
    for t in p.tables:
        if len(t["rows"]) < 3:
            continue
        grid = expand(t)
        if not grid or not grid[0] or not grid[0][0].lower().startswith("district"):
            continue
        if any("this race" in h.lower() for h in grid[0]):
            continue  # special elections held earlier in 2026 and non-voting delegates: not the Nov 3 general
        header = grid[1] if len(grid) > 1 else []
        ci_loc = 0
        ci_status = find_col(header, "status", "results")
        ci_cand = find_col(header, "candidates")
        if ci_status is None or ci_cand is None:
            continue
        for row in grid[2:]:
            if len(row) <= max(ci_status, ci_cand):
                continue
            key, name = house_key(row[ci_loc])
            if not key:
                continue
            cands = parse_candidates(row[ci_cand])
            prev = races.get(key)
            status = row[ci_status]
            if prev:  # a district with two incumbent rows (redistricting): merge status lines
                if status not in prev["status_text"]:
                    prev["status_text"] += " | " + status
                continue
            races[key] = {"key": key, "name": name, "chamber": "House", "status_text": status, "candidates": cands}
    for r in races.values():
        r.update(race_status(r["status_text"], r["candidates"]))
    return races


def parse_senate(html):
    p = collect.TableParser()
    p.feed(html)
    races, up_party = {}, {"D": 0, "R": 0, "I": 0}
    for t in p.tables:
        if len(t["rows"]) < 3:
            continue
        grid = expand(t)
        # 2026 pre-election tables start "Constituency"; the 2024 results tables start "State".
        if not grid or not grid[0] or not grid[0][0].lower().startswith(("constituency", "state")):
            continue
        header = grid[1]
        if not any("incumbent" in h.lower() or "senator" in h.lower() for h in grid[0] + header):
            continue  # e.g. the filing-deadline table, which also starts with "State"
        ci_status = find_col(header, "result", "status")  # "Results" (2026) or "Result" (2024)
        ci_cand = find_col(header, "candidates")
        ci_party = find_col(header, "party")
        if ci_status is None or ci_cand is None:
            continue
        special_table = "status" in header[ci_status].lower() and "result" not in header[ci_status].lower()
        for row in grid[2:]:
            if len(row) <= max(ci_status, ci_cand):
                continue
            special = special_table or "special" in row[0].lower()
            key, name = senate_key(row[0], special)
            if not key or key in races:
                continue
            cands = parse_candidates(row[ci_cand])
            inc_party = party_code(row[ci_party]) if ci_party is not None else "?"
            up_party[inc_party] = up_party.get(inc_party, 0) + 1
            races[key] = {"key": key, "name": name, "chamber": "Senate", "status_text": row[ci_status],
                          "candidates": cands, "incumbent_party": inc_party}
    for r in races.values():
        r.update(race_status(r["status_text"], r["candidates"]))
    return races, up_party


# --------------------------------------------------------------------------- frozen ratings

def frozen_ratings(cutoff):
    """Consensus scores from the last raw snapshot before election day (the 'final' ratings)."""
    dirs = sorted(d for d in glob.glob(os.path.join(ROOT, "data", "raw", "20*"))
                  if os.path.basename(d) < cutoff and os.path.exists(os.path.join(d, "ratings_house.json")))
    if not dirs:
        return None, {}
    d = dirs[-1]
    out = {}
    for f in ("ratings_house.json", "ratings_senate.json"):
        data = load(os.path.join(d, f), {}) or {}
        for r in data.get("races", []):
            scores = sorted(RATING_SCORE[v["rating"]] for v in r.get("ratings", {}).values()
                            if v and v.get("rating") in RATING_SCORE)
            if not scores:
                continue
            n = len(scores)
            med = scores[n // 2] if n % 2 else (scores[n // 2 - 1] + scores[n // 2]) / 2
            if med != int(med):  # ties toward Tossup
                med = int(med - 0.5) if med > 0 else int(med + 0.5)
            out[r["key"]] = {"score": int(med), "label": r.get("name") or r.get("label"),
                             "state": r.get("state"), "district": r.get("district"), "chamber": r.get("chamber"),
                             "incumbent": r.get("incumbent"), "open_seat": r.get("open_seat")}
    return os.path.basename(d), out


SCORE_LABEL = {v: k for k, v in RATING_SCORE.items()}


# --------------------------------------------------------------------------- news

def gnews(query, cutoff_iso, max_items=6):
    gn = collect.CONFIG["google_news"]
    url = gn["template"].format(query=urllib.parse.quote(query))
    status, body = collect.fetch(url, accept="application/rss+xml, application/xml, text/xml", timeout=30)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    _, items = collect.parse_feed(body)
    kept = []
    for it in items:
        if it["published"] and it["published"] < cutoff_iso:
            continue
        m = re.match(r"^(.*)\s-\s([^-]+)$", it["title"])
        title = m.group(1).strip() if m else it["title"]
        kept.append({"title": title, "outlet": it["source_title"] or (m.group(2).strip() if m else ""),
                     "link": it["link"], "published": it["published"],
                     "flags": [f for f, rx in (("call", CALL_HL), ("uncertain", UNCERTAIN_HL), ("legal", LEGAL_HL)) if rx.search(title)]})
        if len(kept) >= max_items:
            break
    return kept


def race_news_query(r, meta):
    suffix = CFG.get("race_query_suffix", "election results")
    if r["chamber"] == "Senate":
        state = re.sub(r" Senate.*$", "", r["name"])
        return f'"{state}" Senate {suffix}'
    abbr, d = r["key"].split("-")[1], r["key"].split("-")[2]
    state = collect.STATE_BY_ABBR.get(abbr, abbr)
    if d == "AL":
        return f'"{state}" at-large congressional {suffix}'
    n = int(d)
    ordinal = f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    return f'"{state}" "{ordinal} congressional district" {suffix}'


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--edition", choices=["am", "pm"])
    ap.add_argument("--skip-news", action="store_true")
    ap.add_argument("--html-dir", help="read house.html / senate.html from this directory instead of fetching (testing)")
    args = ap.parse_args()
    now_c = chicago_now()
    run_date = args.date or now_c.date().isoformat()
    edition = args.edition or ("am" if now_c.hour < 12 else "pm")
    raw_dir = os.path.join(ROOT, "data", "raw", run_date)
    os.makedirs(raw_dir, exist_ok=True)
    problems = []

    # 1. Wikipedia results tables
    pages = {}
    for which in ("house", "senate"):
        try:
            if args.html_dir:
                pages[which] = open(os.path.join(args.html_dir, f"{which}.html"), encoding="utf-8").read()
            else:
                status, body = collect.fetch(CFG["sources"][which], accept="text/html")
                if status != 200:
                    raise RuntimeError(f"HTTP {status}")
                pages[which] = body.decode("utf-8", "replace")
                time.sleep(2)
        except Exception as e:  # noqa: BLE001
            problems.append(f"Wikipedia {which} results page: {type(e).__name__}: {e}")
    house = parse_house(pages["house"]) if "house" in pages else {}
    senate, up_party = parse_senate(pages["senate"]) if "senate" in pages else ({}, {})
    if "house" in pages and len(house) < 400:
        problems.append(f"House results table parsed only {len(house)} of 435 districts; the article layout may have changed")
    if "senate" in pages and len(senate) < 33:
        problems.append(f"Senate results table parsed only {len(senate)} races; the article layout may have changed")

    # 2. frozen pre-election ratings
    freeze_date, ratings = frozen_ratings(CFG["post_election_from"])
    if not ratings:
        problems.append("no pre-election ratings snapshot found; cannot separate competitive from safe seats")

    races = {**house, **senate}
    for k, r in races.items():
        meta = ratings.get(k)
        r["final_rating"] = SCORE_LABEL.get(meta["score"]) if meta else None
        r["final_score"] = meta["score"] if meta else None
        # Forecasters rate every seat with any chance of flipping; an unrated seat is a safe seat.
        r["competitive"] = meta is not None and abs(meta["score"]) <= CFG.get("competitive_max_abs_score", 3)
        # A seat rated safe whose uncalled count is close gets full coverage too (possible upset).
        r["upset_watch"] = (not r["competitive"] and not r["called"] and r["margin_pts"] is not None
                            and r["margin_pts"] <= CFG.get("upset_watch_points", 5))

    # 3. previous snapshot -> newly called
    snap_path = os.path.join(ROOT, "data", "results", f"{run_date}-{edition}.json")
    prev_files = sorted(f for f in glob.glob(os.path.join(ROOT, "data", "results", "*.json")) if f != snap_path)
    prev = load(prev_files[-1], {}) if prev_files else {}
    prev_called = {k for k, v in (prev.get("races") or {}).items() if v.get("called")}
    prev_label = os.path.basename(prev_files[-1])[:-5] if prev_files else None
    newly_called = sorted(k for k, r in races.items() if r["called"] and k not in prev_called)
    uncalled_after_call = sorted(k for k in prev_called if k in races and not races[k]["called"])  # table reverted

    # 4. news
    lookback = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CFG.get("news_lookback_days", 3))).isoformat()
    uncalled_comp = sorted([k for k, r in races.items() if not r["called"] and (r["competitive"] or r["upset_watch"])],
                           key=lambda k: (races[k]["chamber"] != "Senate", abs(races[k]["final_score"] or 0), k))
    pending_safe = sorted(k for k, r in races.items() if not r["called"] and not r["competitive"] and not r["upset_watch"])
    news = {"races": {}, "national": {}, "errors": []}
    if not args.skip_news:
        targets = uncalled_comp + [k for k in newly_called if races[k]["competitive"] and k not in uncalled_comp]
        for k in targets[: CFG.get("max_race_queries", 150)]:
            q = race_news_query(races[k], ratings.get(k))
            try:
                news["races"][k] = {"query": q, "items": gnews(q, lookback)}
            except Exception as e:  # noqa: BLE001
                news["errors"].append(f"{k}: {type(e).__name__}: {e}")
            time.sleep(0.4)
        for group, qs in CFG.get("national_queries", {}).items():
            news["national"][group] = []
            for q in qs:
                try:
                    news["national"][group].append({"query": q, "items": gnews(q, lookback, 8)})
                except Exception as e:  # noqa: BLE001
                    news["errors"].append(f"{group}/{q}: {type(e).__name__}: {e}")
                time.sleep(0.4)
        dump(news, os.path.join(raw_dir, f"results_news_{edition}.json"))
        if news["errors"]:
            problems.append(f"{len(news['errors'])} Google News queries failed (first: {news['errors'][0]})")

    # headline leads for uncalled races
    for k in uncalled_comp:
        items = (news["races"].get(k) or {}).get("items", [])
        races[k]["headline_call_lead"] = any("call" in i["flags"] and "uncertain" not in i["flags"] for i in items)
        races[k]["headline_uncertain"] = any("uncertain" in i["flags"] for i in items)

    # 5. tallies
    def tally(chamber):
        rs = [r for r in races.values() if r["chamber"] == chamber]
        won = {"D": 0, "R": 0, "I": 0, "Other": 0}
        for r in rs:
            if r["called"] and r["winner"]:
                p = r["winner"]["party"]
                won[p if p in won else "Other"] += 1
        return {"races": len(rs), "called": sum(1 for r in rs if r["called"]), "won": won,
                "uncalled": sum(1 for r in rs if not r["called"])}
    house_t, senate_t = tally("House"), tally("Senate")
    not_up = {"D": SENATE_COMPOSITION["D"] - up_party.get("D", 0) - up_party.get("I", 0),
              "R": SENATE_COMPOSITION["R"] - up_party.get("R", 0)} if senate else None
    senate_t["not_up"] = not_up
    if not_up:
        senate_t["seats_secured"] = {"D": not_up["D"] + senate_t["won"]["D"], "R": not_up["R"] + senate_t["won"]["R"],
                                     "I": senate_t["won"]["I"]}
        senate_t["majority"] = 51
    house_t["majority"] = 218

    close = []
    for k, r in races.items():
        if r["margin_pts"] is not None and r["margin_pts"] <= max(CFG.get("close_margin_points", [1.0])):
            close.append(k)

    snapshot = {"run_date": run_date, "edition": edition, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "previous_snapshot": prev_label, "ratings_frozen_from": freeze_date,
                "sources": CFG["sources"], "house": house_t, "senate": senate_t,
                "newly_called": newly_called, "uncalled_competitive": uncalled_comp, "pending_safe": pending_safe,
                "reverted_to_uncalled": uncalled_after_call, "close_margins": sorted(close),
                "problems": problems, "races": races}
    if races:
        dump(snapshot, snap_path)
    dump({**snapshot, "news": {k: v["items"][:3] for k, v in news["races"].items()}}, os.path.join(ROOT, "docs", "data", "results.json"))

    # 6. digest section
    write_digest(run_date, edition, snapshot, news)
    print(f"results: House {house_t['called']}/{house_t['races']} called, Senate {senate_t['called']}/{senate_t['races']} called, "
          f"{len(newly_called)} newly called, {len(uncalled_comp)} competitive uncalled, {len(pending_safe)} safe seats not yet marked; "
          f"{len(problems)} problems", file=sys.stderr)
    return 0


def wlabel(r):
    w = r.get("winner")
    if not w:
        return "?"
    pct = f" {w['pct']:.1f}%" if w.get("pct") is not None else ""
    return f"{w['name']} ({w['party']}){pct}"


def lead_label(r):
    cands = sorted([c for c in r["candidates"] if c["pct"] is not None], key=lambda c: -c["pct"])
    if len(cands) >= 2:
        return f"{cands[0]['name']} ({cands[0]['party']}) {cands[0]['pct']:.1f}% vs {cands[1]['name']} ({cands[1]['party']}) {cands[1]['pct']:.1f}%, margin {r['margin_pts']} pts (Wikipedia table; may be partial)"
    order = {"D": 0, "R": 1, "I": 2}
    major = sorted(r["candidates"], key=lambda c: order.get(c["party"], 3))
    names = ", ".join(f"{c['name']} ({c['party']})" for c in major[:3])
    return f"no vote shares in the table yet; candidates {names}" if names else "no candidates parsed"


def write_digest(run_date, edition, s, news):
    path = os.path.join(ROOT, "data", "bundle", f"{run_date}.md")
    existing = open(path, encoding="utf-8").read() if os.path.exists(path) else f"# Tracker digest for {run_date}\n"
    existing = re.split(r"\n## Results \(post-election\)", existing)[0].rstrip() + "\n"
    races = s["races"]
    L = []
    L.append(f"\n## Results (post-election), {edition.upper()} edition\n")
    L.append(f"Source tables: House {s['sources']['house']} ; Senate {s['sources']['senate']} . Wikipedia editors mark a race called (Y winner mark or an outcome line) when AP or the networks call it. "
             f"Treat a table call as 'reported called'; for any race you write up, cite a news link that confirms the call when one is in the leads below. "
             f"Previous snapshot: {s['previous_snapshot'] or 'none (first post-election run)'}. Pre-election ratings frozen from {s['ratings_frozen_from']}.")
    h, se = s["house"], s["senate"]
    L.append(f"\n### Chamber status\n- House: {h['called']} of {h['races']} called: D {h['won']['D']}, R {h['won']['R']}"
             + (f", other {h['won']['I'] + h['won']['Other']}" if h['won']['I'] + h['won']['Other'] else "")
             + f"; {h['uncalled']} not marked called. Majority is 218.")
    if se.get("seats_secured"):
        ss = se["seats_secured"]
        L.append(f"- Senate: {se['called']} of {se['races']} races called. Seats secured including those not up this year: D {ss['D']}, R {ss['R']}"
                 + (f", independent {ss['I']}" if ss['I'] else "") + f" ({se['not_up']['D']} D and {se['not_up']['R']} R not up). Majority is 51 (the vice president breaks 50-50 ties).")
    L.append(f"- Safe seats (final consensus Solid) not yet marked called in the table: {len(s['pending_safe'])}. These are usually table lag, not real uncertainty (none has a count within 5 points); give them as a count in the brief, not race by race. Keys: " + (", ".join(s["pending_safe"]) or "none"))

    L.append(f"\n### Called since the last update ({len(s['newly_called'])})")
    comp_new = [k for k in s["newly_called"] if races[k]["competitive"]]
    safe_new = [k for k in s["newly_called"] if not races[k]["competitive"]]
    for k in comp_new:
        r = races[k]
        L.append(f"- {k} {r['name']} | final rating {r['final_rating'] or 'unrated'} | winner {wlabel(r)} | table: {r['status_text'][:120]}")
        for it in (news["races"].get(k) or {}).get("items", [])[:3]:
            L.append(f"  - lead [{','.join(it['flags']) or '-'}] {it['title']} | {it['outlet']} | {it['published'][:10] if it['published'] else ''} | {it['link']}")
    if safe_new:
        L.append(f"- Plus {len(safe_new)} safe or likely seats called as rated (no detail needed): " + ", ".join(safe_new[:60]) + (" ..." if len(safe_new) > 60 else ""))
    if s["reverted_to_uncalled"]:
        L.append(f"- Marked called last time but not now (table edit; verify before reporting): {', '.join(s['reverted_to_uncalled'])}")

    L.append(f"\n### Competitive races not yet called ({len(s['uncalled_competitive'])}), final rating within Likely D to Likely R, plus safe-rated seats whose count is within 5 points")
    for k in s["uncalled_competitive"]:
        r = races[k]
        tags = []
        if r.get("upset_watch"):
            tags.append("RATED SAFE BUT THE COUNT IS CLOSE")
        if r.get("headline_call_lead"):
            tags.append("HEADLINE SUGGESTS A CALL: verify")
        if r.get("headline_uncertain"):
            tags.append("headlines say close/counting")
        if k in s["close_margins"]:
            tags.append(f"margin {r['margin_pts']} pts")
        L.append(f"- {k} {r['name']} | final rating {r['final_rating'] or 'unrated'} | {lead_label(r)}" + (f" | {'; '.join(tags)}" if tags else ""))
        for it in (news["races"].get(k) or {}).get("items", [])[:4]:
            L.append(f"  - lead [{','.join(it['flags']) or '-'}] {it['title']} | {it['outlet']} | {it['published'][:10] if it['published'] else ''} | {it['link']}")

    if s["close_margins"]:
        L.append("\n### Margins of 1 point or less in the table (check the state's recount law before writing 'recount')")
        for k in s["close_margins"]:
            r = races[k]
            L.append(f"- {k} {r['name']}: {lead_label(r)}; called: {'yes' if r['called'] else 'no'}")

    titles = {"chamber_calls": "Chamber-control calls", "uncalled_and_counting": "Counting and uncalled races",
              "recounts_and_certification": "Recounts and certification", "legal_and_federal": "Legal and federal action",
              "exit_polls": "Exit polls and voter surveys"}
    for group, title in titles.items():
        L.append(f"\n### {title} (headline leads; verify and cite)")
        seen = set()
        n = 0
        for block in news["national"].get(group, []):
            for it in block["items"]:
                if it["link"] in seen or it["title"] in seen:
                    continue
                seen.update([it["link"], it["title"]])
                L.append(f"- [{','.join(it['flags']) or '-'}] {it['title']} | {it['outlet']} | {it['published'][:10] if it['published'] else ''} | {it['link']}")
                n += 1
        if not n:
            L.append("- none found")

    L.append("\n### Results coverage")
    L.append(f"- House rows parsed: {h['races']}; Senate rows parsed: {se['races']}; race news queries: {len(news['races'])}; news errors: {len(news['errors'])}")
    for p in s["problems"]:
        L.append(f"- PROBLEM: {p}")
    if not s["problems"]:
        L.append("- No problems.")
    L.append("- Court dockets are not pulled directly (CourtListener's API needs an account key). Legal developments come from the news leads; say so if a court record matters.")
    open(path, "w", encoding="utf-8").write(existing + "\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
