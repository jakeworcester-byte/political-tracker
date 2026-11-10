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
        m0 = re.match(r"^Y(?:\s+|(?=[A-Z]))", chunk)  # {{Aye}} mark, with or without a following space
        if m0:
            winner, chunk = True, chunk[m0.end():].strip()
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


def infer_winner(status_text, cands, inc_party):
    """Winner implied by an outcome line when editors wrote the outcome but no winner mark.
    Only returns a candidate when the outcome identifies exactly one (e.g. 'Democratic gain' with one
    Democrat on the ballot), so top-two same-party races stay uncalled."""
    t = status_text or ""
    m = re.search(r"\b(democratic|republican|independent)\S* (?:gain|hold)\b", t, re.I)
    if m:
        p = {"democratic": "D", "republican": "R", "independent": "I"}[m.group(1).lower()]
        same = [c for c in cands if c["party"] == p]
        return same[0] if len(same) == 1 else None
    if re.search(r"\blost re-?election\b", t, re.I) and inc_party in ("D", "R"):
        other = [c for c in cands if c["party"] in ("D", "R") and c["party"] != inc_party]
        return other[0] if len(other) == 1 else None
    if re.search(r"re-?elected", t, re.I):
        if len(cands) == 1:
            return cands[0]
        if inc_party in ("D", "R", "I"):
            same = [c for c in cands if c["party"] == inc_party]
            return same[0] if len(same) == 1 else None
    return None


def race_status(status_text, cands, inc_party=None):
    called = any(c["winner"] for c in cands)
    outcome = bool(OUTCOME_RE.search(status_text or "")) and not re.search(r"to be elected", status_text or "", re.I)
    winner = next((c for c in cands if c["winner"]), None)
    if not winner and outcome:
        with_pct = [c for c in cands if c["pct"] is not None]
        if len(with_pct) >= 1:
            winner = max(with_pct, key=lambda c: c["pct"])
        else:
            winner = infer_winner(status_text, cands, inc_party)
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
        ci_party = find_col(header, "party")
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
            inc_party = party_code(row[ci_party]) if ci_party is not None and ci_party < len(row) else None
            races[key] = {"key": key, "name": name, "chamber": "House", "status_text": status, "candidates": cands,
                          "incumbent_party": inc_party}
    for r in races.values():
        r.update(race_status(r["status_text"], r["candidates"], r.get("incumbent_party")))
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
        r.update(race_status(r["status_text"], r["candidates"], r.get("incumbent_party")))
    return races, up_party


# --------------------------------------------------------------------------- governors

def norm_rating(s):
    t = re.sub(r"\(.*?\)", "", s or "").strip()
    t = re.sub(r"^Safe\b", "Solid", t)
    return t if t in RATING_SCORE else None


def median_score(scores):
    scores = sorted(scores)
    n = len(scores)
    med = scores[n // 2] if n % 2 else (scores[n // 2 - 1] + scores[n // 2]) / 2
    if med != int(med):  # ties toward Tossup
        med = int(med - 0.5) if med > 0 else int(med + 0.5)
    return int(med)


def parse_governors(html):
    """Gubernatorial article: the ratings table (Constituency/State ... forecaster columns ... Result) and the
    race summary table (State, Governor, Party, First elected, Status, Candidates). States only; territories skipped."""
    p = collect.TableParser()
    p.feed(html)
    races, ratings = {}, {}
    for t in p.tables:
        if len(t["rows"]) < 10:
            continue
        grid = expand(t)
        top = [h.lower() for h in grid[0]]
        if top and top[0].startswith("constituency") and len(grid) > 2:
            header = grid[1]
            lo = find_col(header, "last election")
            hi = find_col(header, "result")
            if lo is None or hi is None:
                continue
            cols = list(range(lo + 1, hi))
            ratings["_forecasters"] = [header[i] for i in cols]
            for row in grid[2:]:
                abbr = collect.STATES.get(row[0].strip())
                if not abbr or len(row) < hi:
                    continue
                per = {header[i]: norm_rating(row[i]) for i in cols}
                scores = [RATING_SCORE[v] for v in per.values() if v]
                if scores:
                    ratings[abbr] = {"score": median_score(scores), "by_forecaster": per}
        elif top[:2] == ["state", "governor"] and any("candidates" in h for h in top):
            ci_party, ci_status, ci_cand = find_col(grid[0], "party"), find_col(grid[0], "status"), find_col(grid[0], "candidates")
            for row in grid[1:]:
                abbr = collect.STATES.get(row[0].strip())
                if not abbr or len(row) <= max(ci_status, ci_cand):
                    continue
                txt = row[ci_cand]
                cands = parse_candidates(txt[txt.find("▌"):] if "▌" in txt else txt)
                inc_party = party_code(row[ci_party])
                races[abbr] = {"key": f"GOV-{abbr}", "state": row[0].strip(), "name": f"{row[0].strip()} governor",
                               "incumbent": row[1].strip(), "incumbent_party": inc_party, "status_text": row[ci_status],
                               "candidates": cands}
    for r in races.values():
        r.update(race_status(r["status_text"], r["candidates"], r["incumbent_party"]))
    return races, ratings


def parse_current_governors(html):
    """List of current United States governors: state -> party (for the states with no 2026 race)."""
    p = collect.TableParser()
    p.feed(html)
    out = {}
    for t in p.tables:
        grid = expand(t)
        if len(grid) < 40 or not grid[0] or not grid[0][0].lower().startswith("state"):
            continue
        ci_gov = find_col(grid[0], "governor")
        parties = [i for i, h in enumerate(grid[0]) if h.lower().startswith("party")]
        for row in grid[1:]:
            abbr = collect.STATES.get(re.sub(r"\s*\(list\)\s*$", "", row[0]).strip())
            if not abbr or ci_gov is None:
                continue
            party = next((party_code(row[i]) for i in reversed(parties) if i < len(row) and row[i].strip()), "?")
            out[abbr] = {"governor": row[ci_gov].strip(), "party": party}
        if out:
            break
    return out


def governors_block(races, ratings, current, freeze_date):
    states, won = {}, {"D": 0, "R": 0, "I": 0, "Other": 0}
    before = {"D": 0, "R": 0, "I": 0, "Other": 0}
    not_up = {"D": 0, "R": 0, "I": 0, "Other": 0}
    flips = []
    bucket = lambda p: p if p in won else "Other"  # noqa: E731
    for abbr, r in races.items():
        meta = ratings.get(abbr) or {}
        w = r["winner"] if r["called"] else None
        flip = bool(w and r["incumbent_party"] in ("D", "R", "I") and w["party"] != r["incumbent_party"])
        states[abbr] = {"up": True, "state": r["state"], "incumbent": r["incumbent"], "incumbent_party": r["incumbent_party"],
                        "called": r["called"], "winner": ({"name": w["name"], "party": w["party"], "pct": w["pct"]} if w else None),
                        "leader": ({"name": r["leader"]["name"], "party": r["leader"]["party"]} if r.get("leader") and not w else None),
                        "margin_pts": r["margin_pts"], "status_text": r["status_text"],
                        "final_rating": SCORE_LABEL.get(meta.get("score")) if meta else None,
                        "final_score": meta.get("score"), "flip": flip}
        before[bucket(r["incumbent_party"])] += 1
        if w:
            won[bucket(w["party"])] += 1
        if flip:
            flips.append({"state": abbr, "from": r["incumbent_party"], "to": w["party"], "winner": w["name"]})
    for abbr, g in current.items():
        if abbr in states:
            continue
        states[abbr] = {"up": False, "state": next((k for k, v in collect.STATES.items() if v == abbr), abbr),
                        "governor": g["governor"], "party": g["party"]}
        not_up[bucket(g["party"])] += 1
        before[bucket(g["party"])] += 1
    called = sum(1 for r in races.values() if r["called"])
    held = {p: won[p] + not_up[p] for p in won}
    return {"races": len(races), "called": called, "uncalled": len(races) - called, "won": won, "not_up": not_up,
            "seats_secured": held, "before": before, "flips": sorted(flips, key=lambda f: f["state"]),
            "ratings_frozen_from": freeze_date, "forecasters": ratings.get("_forecasters", []),
            "sources": {"results": CFG["sources"].get("governors"), "not_up": CFG["sources"].get("governors_current")},
            "states": dict(sorted(states.items()))}


def call_timeline(current_label, current_races):
    """Competitive House and Senate races marked called, by results snapshot."""
    out = []
    files = sorted(glob.glob(os.path.join(ROOT, "data", "results", "20*.json")))
    seen = set()
    for f in files:
        label = os.path.basename(f)[:-5]
        if label == current_label:
            continue
        rs = (load(f, {}) or {}).get("races", {})
        out.append((label, {k for k, r in rs.items() if r.get("competitive") and r.get("called")}))
    out.append((current_label, {k for k, r in current_races.items() if r.get("competitive") and r.get("called")}))
    rows = []
    for label, called in sorted(out):
        rows.append({"snapshot": label, "competitive_called": len(called), "new": len(called - seen)})
        seen |= called
    return {"competitive_total": sum(1 for r in current_races.values() if r.get("competitive")), "snapshots": rows}


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


# --------------------------------------------------------------------------- news-reported calls
# Wikipedia's tables lag AP by hours on election night. A race is "reported called" when news headlines
# (Google News RSS, already used for leads) name a winner in call language after polls closed: either one
# headline citing a projection (AP, "projects", a network results page titled "... Wins") or two different
# outlets agreeing. Victory claims, "leads", primaries and conflicting headlines never count. Reported calls
# are kept separate from table calls and always carry their sources.
CALL_VERB = re.compile(r"\b(wins?|won|defeats?|defeated|unseats?|ousts?|flips?|re-?elected|elected|beats?|"
                       r"secures? (?:a |the )?(?:victory|win|seat|re-?election)|holds? (?:on to )?(?:his |her |the )?seat|retains?|declared (?:the )?winner|concedes?|conceded)\b", re.I)
NOT_CALL = re.compile(r"declares? victory|claims? victory|\bleads?\b|leading|\bahead\b|too close|\bcould\b|\bwould\b|\bmight\b|"
                      r"\bodds\b|\bpolls?\b|what to watch|\?|primary|nominat|endorse|debate|runoff|fundrais", re.I)
STRONG_CALL = re.compile(r"\bAP\b|\bprojects?\b|\bprojected\b|(?:live )?results:? .{0,60}\b(?:wins|elected)\b", re.I)


def reported_call(race, items, polls_closed_iso):
    cands = race.get("candidates") or []
    claims = []
    for it in items:
        t = it.get("title") or ""
        if (it.get("published") or "") < polls_closed_iso or not CALL_VERB.search(t) or NOT_CALL.search(t):
            continue
        pos = []
        for c in cands:
            last = c["name"].split()[-1].strip(".,") if c.get("name") else ""
            if len(last) >= 3:
                i = t.find(last)
                if i >= 0:
                    pos.append((i, c))
        if not pos:
            continue
        passive = re.search(r"\b(?:is |was )?(?:defeated|beaten|ousted|unseated) by\b|\blos(?:es|t) to\b|\bfalls? to\b|\bconcedes? to\b", t, re.I)
        if passive:
            after = [(i, c) for i, c in pos if i > passive.start()]
            if not after:
                continue
            winner = min(after, key=lambda x: x[0])[1]  # the candidate named after "defeated by" / "loses to"
        elif re.search(r"\bconced(?:es|ed|e)\b", t, re.I) and not re.search(r"declared (?:the )?winner|\b(?:wins|defeats|beats)\b", t, re.I):
            # "X concedes": X lost. Credit the other major candidate only when the race has exactly two.
            m = re.search(r"\bconced", t, re.I)
            before = [(i, c) for i, c in pos if i < m.start()]
            if not before:
                continue
            loser = max(before, key=lambda x: x[0])[1]
            others = [c for c in cands if c["party"] in ("D", "R", "I") and c is not loser]
            if len(others) != 1:
                continue
            winner = others[0]
        else:
            # first-named candidate, skipping one named just before "concedes" (e.g. "X concedes, Y declared winner")
            cm = re.search(r"\bconced", t, re.I)
            cand_pos = [(i, c) for i, c in pos if not (cm and i < cm.start() and not any(i < j < cm.start() for j, _ in pos))]
            winner = min(cand_pos or pos, key=lambda x: x[0])[1]
        claims.append({"winner": winner, "outlet": it.get("outlet") or "", "title": t, "link": it.get("link"),
                       "strong": bool(STRONG_CALL.search(t))})
    if not claims:
        return None
    names = {c["winner"]["name"] for c in claims}
    if len(names) != 1:
        return None  # headlines disagree: not a call
    outlets = {c["outlet"] for c in claims if c["outlet"]}
    if not (any(c["strong"] for c in claims) or len(outlets) >= 2):
        return None
    return {"winner": claims[0]["winner"], "sources": [{k: c[k] for k in ("outlet", "title", "link")} for c in claims[:3]],
            "basis": "projection cited" if any(c["strong"] for c in claims) else f"{len(outlets)} outlets agree"}


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

    # 2b. governors: results table plus the article's pre-election ratings table, frozen on first read
    gov_block = None
    gov_pages = {}
    for which in ("governors", "governors_current"):
        if args.html_dir:
            path = os.path.join(args.html_dir, f"{which}.html")
            if os.path.exists(path):
                gov_pages[which] = open(path, encoding="utf-8").read()
            continue
        if not CFG["sources"].get(which):
            continue
        try:
            status, body = collect.fetch(CFG["sources"][which], accept="text/html")
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            gov_pages[which] = body.decode("utf-8", "replace")
            time.sleep(2)
        except Exception as e:  # noqa: BLE001
            problems.append(f"Wikipedia {which} page: {type(e).__name__}: {e}")
    if "governors" in gov_pages:
        gov_races, gov_ratings = parse_governors(gov_pages["governors"])
        frozen_path = os.path.join(ROOT, "data", "raw", freeze_date, "ratings_governor.json") if freeze_date else None
        if frozen_path and os.path.exists(frozen_path):
            gov_ratings = (load(frozen_path, {}) or {}).get("ratings", {})
        elif frozen_path and len(gov_ratings) > 30:
            dump({"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": CFG["sources"]["governors"],
                  "note": "Captured from the article's ratings table after Election Day; every column is dated on or before Election Day, so these are the final pre-election ratings.",
                  "ratings": gov_ratings}, frozen_path)
        current = parse_current_governors(gov_pages["governors_current"]) if "governors_current" in gov_pages else {}
        if not current:
            current = {k: {"governor": v.get("governor"), "party": v.get("party")}
                       for k, v in ((load(os.path.join(ROOT, "docs", "data", "results.json"), {}) or {}).get("governors") or {}).get("states", {}).items()
                       if not v.get("up")}
        if len(gov_races) < 34:
            problems.append(f"Governor results table parsed only {len(gov_races)} of 36 races; the article layout may have changed")
        if len(gov_races) + len([k for k in current if k not in gov_races]) != 50:
            problems.append(f"Governor map covers {len(gov_races) + len([k for k in current if k not in gov_races])} of 50 states")
        gov_block = governors_block(gov_races, gov_ratings, current, freeze_date)

    races = {**house, **senate}
    for k, r in races.items():
        meta = ratings.get(k)
        r["final_rating"] = SCORE_LABEL.get(meta["score"]) if meta else None
        r["final_score"] = meta["score"] if meta else None
        # Forecasters rate every seat with any chance of flipping; an unrated seat is a safe seat.
        r["competitive"] = meta is not None and abs(meta["score"]) <= CFG.get("competitive_max_abs_score", 3)
        # A seat rated safe whose uncalled count is close gets full coverage too (possible upset).
        # "Rated safe, now competitive": consensus Solid (or unrated) before the election, count within
        # upset_watch_points now. Flagged whether or not the race has been called.
        r["rated_safe_now_close"] = (not r["competitive"] and r["margin_pts"] is not None
                                     and r["margin_pts"] <= CFG.get("upset_watch_points", 5))
        r["upset_watch"] = r["rated_safe_now_close"] and not r["called"]

    # 3. previous snapshot -> newly called
    snap_path = os.path.join(ROOT, "data", "results", f"{run_date}-{edition}.json")
    prev_files = sorted(f for f in glob.glob(os.path.join(ROOT, "data", "results", "*.json")) if f != snap_path)
    prev = load(prev_files[-1], {}) if prev_files else {}
    prev_called = {k for k, v in (prev.get("races") or {}).items() if v.get("called")}
    prev_label = os.path.basename(prev_files[-1])[:-5] if prev_files else None
    newly_called = sorted(k for k, r in races.items() if r["called"] and k not in prev_called)
    prev_races = prev.get("races") or {}
    for k, r in races.items():
        if r["rated_safe_now_close"]:
            r["flagged_since"] = (prev_races.get(k) or {}).get("flagged_since") or f"{run_date} {edition}"
    rated_safe_close = sorted((k for k, r in races.items() if r["rated_safe_now_close"]),
                              key=lambda k: (races[k]["chamber"] != "Senate", races[k]["margin_pts"], k))
    uncalled_after_call = sorted(k for k in prev_called if k in races and not races[k]["called"])  # table reverted

    # 4. news
    lookback = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CFG.get("news_lookback_days", 3))).isoformat()
    uncalled_comp = sorted([k for k, r in races.items() if not r["called"] and (r["competitive"] or r["upset_watch"])],
                           key=lambda k: (races[k]["chamber"] != "Senate", abs(races[k]["final_score"] or 0), k))
    pending_safe = sorted(k for k, r in races.items() if not r["called"] and not r["competitive"] and not r["upset_watch"])
    news = {"races": {}, "national": {}, "errors": []}
    if not args.skip_news:
        targets = uncalled_comp + [k for k in newly_called if (races[k]["competitive"] or races[k]["rated_safe_now_close"])
                                   and k not in uncalled_comp]
        targets += [k for k in pending_safe if k not in targets]  # safe seats the table hasn't marked: look for reported calls
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

    # news-reported calls for races the table hasn't marked yet
    polls_closed = CFG.get("polls_close_utc", f"{CFG['post_election_from']}T00:00:00+00:00")
    for k, r in races.items():
        r["reported_call"] = None
        if not r["called"]:
            r["reported_call"] = reported_call(r, (news["races"].get(k) or {}).get("items", []), polls_closed)
    reported = sorted(k for k, r in races.items() if r.get("reported_call"))

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
        rep = {"D": 0, "R": 0, "I": 0, "Other": 0}
        for r in rs:
            if not r["called"] and r.get("reported_call"):
                p = r["reported_call"]["winner"]["party"]
                rep[p if p in rep else "Other"] += 1
        return {"races": len(rs), "called": sum(1 for r in rs if r["called"]), "won": won,
                "uncalled": sum(1 for r in rs if not r["called"]),
                "reported": rep, "reported_count": sum(rep.values()),
                "won_incl_reported": {p: won[p] + rep[p] for p in won}}
    house_t, senate_t = tally("House"), tally("Senate")
    not_up = {"D": SENATE_COMPOSITION["D"] - up_party.get("D", 0) - up_party.get("I", 0),
              "R": SENATE_COMPOSITION["R"] - up_party.get("R", 0)} if senate else None
    senate_t["not_up"] = not_up
    if not_up:
        senate_t["seats_secured"] = {"D": not_up["D"] + senate_t["won"]["D"], "R": not_up["R"] + senate_t["won"]["R"],
                                     "I": senate_t["won"]["I"]}
        senate_t["seats_secured_incl_reported"] = {"D": not_up["D"] + senate_t["won_incl_reported"]["D"],
                                                   "R": not_up["R"] + senate_t["won_incl_reported"]["R"],
                                                   "I": senate_t["won_incl_reported"]["I"]}
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
                "reverted_to_uncalled": uncalled_after_call, "close_margins": sorted(close), "rated_safe_now_close": rated_safe_close, "reported_called": reported,
                "governors": gov_block, "call_timeline": call_timeline(f"{run_date}-{edition}", races),
                "problems": problems, "races": races}
    if races:
        dump(snapshot, snap_path)
    dump({**snapshot, "news": {k: v["items"][:3] for k, v in news["races"].items()}}, os.path.join(ROOT, "docs", "data", "results.json"))

    # 6. digest section
    write_digest(run_date, edition, snapshot, news)
    print(f"results: House {house_t['called']}/{house_t['races']} called, Senate {senate_t['called']}/{senate_t['races']} called, "
          f"{len(newly_called)} newly called, {len(uncalled_comp)} competitive uncalled, {len(pending_safe)} safe seats not yet marked, {len(reported)} reported called in news; "
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
    if h.get("reported_count"):
        r_, w_ = h["reported"], h["won_incl_reported"]
        L.append(f"- House, adding races reported called in news but not yet in the table ({h['reported_count']}: D {r_['D']}, R {r_['R']}"
                 + (f", other {r_['I'] + r_['Other']}" if r_['I'] + r_['Other'] else "") + f"): D {w_['D']}, R {w_['R']}. Label these as reported calls with their sources.")
    if se.get("seats_secured"):
        ss = se["seats_secured"]
        L.append(f"- Senate: {se['called']} of {se['races']} races called. Seats secured including those not up this year: D {ss['D']}, R {ss['R']}"
                 + (f", independent {ss['I']}" if ss['I'] else "") + f" ({se['not_up']['D']} D and {se['not_up']['R']} R not up). Majority is 51 (the vice president breaks 50-50 ties).")
    if se.get("seats_secured_incl_reported") and se.get("reported_count"):
        x = se["seats_secured_incl_reported"]
        L.append(f"- Senate, adding races reported called in news but not yet in the table ({se['reported_count']}): D {x['D']}, R {x['R']}.")
    L.append(f"- Safe seats (final consensus Solid) not yet marked called in the table: {len(s['pending_safe'])} ({sum(1 for k in s['pending_safe'] if races[k].get('reported_call'))} of them reported called in news). These are usually table lag, not real uncertainty (none has a count within 5 points); give them as a count in the brief, not race by race. Keys: " + (", ".join(s["pending_safe"]) or "none"))

    rep = s.get("reported_called") or []
    if rep:
        comp_rep = [k for k in rep if races[k]["competitive"] or races[k].get("upset_watch")]
        L.append(f"\n### Reported called in news, not yet marked in the table ({len(rep)}; {len(comp_rep)} competitive)")
        L.append("Basis: headlines after polls closed naming a winner in call language, with a projection cited (AP, network) or two outlets agreeing; conflicts and victory claims excluded. Attribute each to its outlet, e.g. 'NBC News reports X won'; do not call it yourself.")
        for k in comp_rep + [k for k in rep if k not in comp_rep]:
            r = races[k]; rc = r["reported_call"]; w = rc["winner"]
            src = "; ".join(f"{x['outlet']}: {x['title'][:90]} | {x['link']}" for x in rc["sources"][:2])
            L.append(f"- {k} {r['name']} | final rating {r['final_rating'] or 'unrated'} | reported winner {w['name']} ({w['party']}) | basis: {rc['basis']} | {src}")

    g = s.get("governors")
    if g:
        st = g["states"]
        L.append(f"\n### Governors ({g['called']} of {g['races']} races called; source {g['sources']['results']})")
        L.append(f"- Governorships held after this election, counting the {sum(g['not_up'].values())} states not voting this year: D {g['seats_secured']['D']}, R {g['seats_secured']['R']}"
                 + (f", other {g['seats_secured']['I'] + g['seats_secured']['Other']}" if g['seats_secured']['I'] + g['seats_secured']['Other'] else "")
                 + f" (before: D {g['before']['D']}, R {g['before']['R']}). Optional in the brief; cite the table if you mention it.")
        if g["flips"]:
            L.append("- Flips: " + "; ".join(f"{f['state']} {f['from']} to {f['to']} ({f['winner']})" for f in g["flips"]))
        for abbr, v in st.items():
            if v.get("up") and not v["called"]:
                lead = f"{v['leader']['name']} ({v['leader']['party']}) leads by {v['margin_pts']} pts in the table" if v.get("leader") and v.get("margin_pts") is not None else "no vote shares in the table"
                L.append(f"- Uncalled: {v['state']} | final rating {v['final_rating'] or 'unrated'} | {lead} | table: {v['status_text'][:100]}")

    flagged = s.get("rated_safe_now_close") or []
    L.append(f"\n### Rated safe, now competitive ({len(flagged)}): final consensus Solid or unrated, count within {CFG.get('upset_watch_points', 5)} points")
    if flagged:
        L.append("Flag each of these in the update with \"(rated safe)\" and mention the count in the Bottom line if any exist. Margins are from the table and may be partial.")
        for k in flagged:
            r = races[k]
            state = f"called for {wlabel(r)}" if r["called"] else f"not called; {lead_label(r)}"
            L.append(f"- {k} {r['name']} | final rating {r['final_rating'] or 'unrated'} | margin {r['margin_pts']} pts | {state} | flagged since {r.get('flagged_since')}")
    else:
        L.append("- none: every seat rated safe is either uncounted in the table or more than 5 points apart.")

    L.append(f"\n### Called since the last update ({len(s['newly_called'])})")
    comp_new = [k for k in s["newly_called"] if races[k]["competitive"] or races[k].get("rated_safe_now_close")]
    safe_new = [k for k in s["newly_called"] if k not in comp_new]
    for k in comp_new:
        r = races[k]
        flag = " | RATED SAFE, CAME IN CLOSE" if r.get("rated_safe_now_close") else ""
        L.append(f"- {k} {r['name']} | final rating {r['final_rating'] or 'unrated'} | winner {wlabel(r)}{flag} | table: {r['status_text'][:120]}")
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
        if r.get("reported_call"):
            tags.append(f"REPORTED CALLED for {r['reported_call']['winner']['name']} ({r['reported_call']['winner']['party']}) per {r['reported_call']['sources'][0]['outlet']}")
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
              "exit_polls": "Exit polls and voter surveys",
              "who_voted": "Who voted: voter-file turnout analysis (Ralston, Bitzer, major outlets)"}
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
    L.append("- Court dockets come from scripts/courts.py (CourtListener), which appends its own section after this one; legal developments otherwise come from the news leads.")
    open(path, "w", encoding="utf-8").write(existing + "\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
