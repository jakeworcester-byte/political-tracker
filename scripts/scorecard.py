#!/usr/bin/env python3
"""Lessons-learned scorecard: how forecasters, polls, and markets did (stdlib only).

Compares the latest results snapshot (data/results/*.json, written by results.py) with what the
tracker recorded just before Election Day:
  * forecaster ratings: data/raw/<freeze>/ratings_house.json and ratings_senate.json
  * poll averages for tracked races and chamber-control market prices: data/bundle/<freeze>.json
where <freeze> is the last date before config/postelection.json's post_election_from.

A forecaster's call counts as right when it rated a race Tilt or stronger for the party that won,
wrong when it favored the other party, and is set aside when it said Tossup. Poll error is the
actual margin minus the final polling average (both R minus D, so a positive error means the polls
underestimated Republicans). Vote shares come from the Wikipedia results table and are partial until
counting ends, so the scorecard says "preliminary" until 90% of competitive races are called.

Writes docs/data/scorecard.json and appends a "Scorecard" block to the day's digest.
Usage: python3 scripts/scorecard.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from results import CFG, RATING_SCORE, ROOT, chicago_now, dump, load  # noqa: E402

NAMES = {"cook": "Cook Political Report", "ie": "Inside Elections", "sabato": "Sabato's Crystal Ball", "econ": "The Economist",
         "st": "Split Ticket", "ddhq": "Decision Desk HQ", "fpo": "FiftyPlusOne", "fox": "Fox News", "silver": "Silver Bulletin",
         "rcp": "RealClearPolling", "nbc": "NBC News"}


def freeze_date():
    dirs = sorted(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "data", "raw", "20*"))
                  if os.path.basename(d) < CFG["post_election_from"] and os.path.exists(os.path.join(d, "ratings_house.json")))
    return dirs[-1] if dirs else None


def parse_avg(s):
    m = re.match(r"^\s*([DR])\+([\d.]+)", s or "")
    if m:
        v = float(m.group(2))
        return v if m.group(1) == "R" else -v
    if (s or "").startswith("Even"):
        return 0.0
    return None


def actual_margin(race):
    """R minus D using the top Democratic and top Republican candidates in the table."""
    best = {}
    for c in race.get("candidates", []):
        if c.get("pct") is None or c["party"] not in ("D", "R"):
            continue
        if c["pct"] > best.get(c["party"], -1):
            best[c["party"]] = c["pct"]
    if "D" in best and "R" in best:
        return round(best["R"] - best["D"], 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    args = ap.parse_args()
    run_date = args.date or chicago_now().date().isoformat()
    snaps = sorted(glob.glob(os.path.join(ROOT, "data", "results", "*.json")))
    if not snaps:
        print("scorecard: no results snapshot yet", file=sys.stderr)
        return 0
    snap = load(snaps[-1], {})
    races = snap.get("races", {})
    fz = freeze_date()
    if not fz:
        print("scorecard: no pre-election ratings snapshot", file=sys.stderr)
        return 0

    # forecaster records
    fc = {}
    final = {}
    for f in ("ratings_house.json", "ratings_senate.json"):
        for r in (load(os.path.join(ROOT, "data", "raw", fz, f), {}) or {}).get("races", []):
            final[r["key"]] = r
    for key, r in final.items():
        res = races.get(key)
        if not res or not res.get("called") or not res.get("winner"):
            continue
        wp = res["winner"]["party"]
        for code, v in r.get("ratings", {}).items():
            if not v or v.get("rating") not in RATING_SCORE:
                continue
            s = RATING_SCORE[v["rating"]]
            rec = fc.setdefault(code, {"right": 0, "wrong": 0, "tossup": 0, "misses": []})
            if s == 0:
                rec["tossup"] += 1
            elif (s < 0 and wp == "D") or (s > 0 and wp == "R"):
                rec["right"] += 1
            else:
                rec["wrong"] += 1
                rec["misses"].append(f"{key} ({v['rating']}, {wp} won)")
    table = []
    for code, rec in fc.items():
        decided = rec["right"] + rec["wrong"]
        table.append({"forecaster": NAMES.get(code, code), "code": code, "right": rec["right"], "wrong": rec["wrong"],
                      "tossups": rec["tossup"], "accuracy_pct": round(100 * rec["right"] / decided, 1) if decided else None,
                      "misses": rec["misses"]})
    table.sort(key=lambda x: (-(x["accuracy_pct"] or 0), x["wrong"]))

    # consensus record (frozen consensus from results.py)
    cons = {"right": 0, "wrong": 0, "tossup": 0, "misses": []}
    for key, res in races.items():
        if not res.get("called") or not res.get("winner") or res.get("final_score") is None:
            continue
        s, wp = res["final_score"], res["winner"]["party"]
        if s == 0:
            cons["tossup"] += 1
        elif (s < 0 and wp == "D") or (s > 0 and wp == "R"):
            cons["right"] += 1
        else:
            cons["wrong"] += 1
            cons["misses"].append(f"{key} ({res['final_rating']}, {wp} won)")
    tossup_wins = {"D": 0, "R": 0, "Other": 0}
    for res in races.values():
        if res.get("called") and res.get("winner") and res.get("final_score") == 0:
            p = res["winner"]["party"]
            tossup_wins[p if p in tossup_wins else "Other"] += 1

    # what each final consensus rating turned out to mean (House and Senate)
    order = ["Solid D", "Likely D", "Lean D", "Tilt D", "Tossup", "Tilt R", "Lean R", "Likely R", "Solid R"]
    by_rating = {k: {"D": 0, "R": 0, "Other": 0, "uncalled": 0} for k in order}
    upsets = []
    for key, res in races.items():
        fr = res.get("final_rating")
        if fr not in by_rating:
            continue
        if not res.get("called") or not res.get("winner"):
            by_rating[fr]["uncalled"] += 1
            continue
        wp = res["winner"]["party"]
        by_rating[fr][wp if wp in ("D", "R") else "Other"] += 1
        s_ = res.get("final_score") or 0
        if (s_ < 0 and wp != "D") or (s_ > 0 and wp != "R"):
            upsets.append({"key": key, "name": res["name"], "chamber": res["chamber"], "final_rating": fr,
                           "winner": res["winner"]["name"], "party": wp, "incumbent_party": res.get("incumbent_party")})
    by_rating = [{"rating": k, **v} for k, v in by_rating.items() if sum(v.values())]
    upsets.sort(key=lambda u: (u["chamber"] != "Senate", -abs(RATING_SCORE[u["final_rating"]]), u["key"]))

    # seat projections before the election vs seats won
    h_raw = load(os.path.join(ROOT, "data", "raw", fz, "ratings_house.json"), {}) or {}
    s_raw = load(os.path.join(ROOT, "data", "raw", fz, "ratings_senate.json"), {}) or {}
    hs, ss_ = snap.get("house", {}), snap.get("senate", {})
    house_proj = [{"forecaster": v.get("name") or NAMES.get(k, k), "code": k, "D": v.get("D"), "R": v.get("R"),
                   "tossups": v.get("tossups"), "as_of": v.get("as_of")} for k, v in (h_raw.get("overall") or {}).items()]
    not_up = ss_.get("not_up") or {"D": 0, "R": 0}
    sen = {}
    for r in s_raw.get("races", []):
        for code, v in (r.get("ratings") or {}).items():
            if not v or v.get("rating") not in RATING_SCORE:
                continue
            x = sen.setdefault(code, {"D": not_up.get("D", 0), "R": not_up.get("R", 0), "tossups": 0})
            sc = RATING_SCORE[v["rating"]]
            x["D" if sc < 0 else "R" if sc > 0 else "tossups"] += 1
    n_sen = len(s_raw.get("races", []))
    senate_proj = [{"forecaster": NAMES.get(k, k), "code": k, **v} for k, v in sen.items()
                   if v["D"] + v["R"] + v["tossups"] - not_up.get("D", 0) - not_up.get("R", 0) == n_sen]
    projections = {"basis": "Seats each forecaster rated Tilt or stronger for a party, before Election Day. Senate counts include seats not up this year.",
                   "house": {"actual": hs.get("won"), "called": hs.get("called"), "races": hs.get("races"), "majority": 218,
                             "forecasters": sorted(house_proj, key=lambda x: -(x["D"] or 0))},
                   "senate": {"actual": ss_.get("seats_secured"), "called": ss_.get("called"), "races": ss_.get("races"), "majority": 51,
                              "forecasters": sorted(senate_proj, key=lambda x: -x["D"])}}

    # governors (frozen consensus from the article's ratings table)
    gov = (snap.get("governors") or {}).get("states", {})
    gov_score = {"right": 0, "wrong": 0, "tossup": 0, "uncalled": 0, "upsets": []}
    for abbr, g in gov.items():
        if not g.get("up") or g.get("final_score") is None:
            continue
        if not g.get("called") or not g.get("winner"):
            gov_score["uncalled"] += 1
            continue
        sc, wp = g["final_score"], g["winner"]["party"]
        if sc == 0:
            gov_score["tossup"] += 1
        elif (sc < 0 and wp == "D") or (sc > 0 and wp == "R"):
            gov_score["right"] += 1
        else:
            gov_score["wrong"] += 1
            gov_score["upsets"].append({"key": f"GOV-{abbr}", "name": f"{g['state']} governor", "chamber": "Governor",
                                        "final_rating": g["final_rating"], "winner": g["winner"]["name"], "party": wp})

    # polls
    bundle = load(os.path.join(ROOT, "data", "bundle", f"{fz}.json"), {}) or {}
    polls = []
    for row in bundle.get("notion_race_rows", []):
        avg = parse_avg(row.get("Polling average"))
        res = races.get(row["Key"])
        if avg is None or not res or not res.get("called"):
            continue
        act = actual_margin(res)
        if act is None:
            continue
        polls.append({"key": row["Key"], "race": row["Race"], "poll_avg": avg, "actual": act, "error": round(act - avg, 1)})
    poll_summary = None
    if polls:
        errs = [p["error"] for p in polls]
        poll_summary = {"races": len(polls), "mean_abs_error": round(statistics.mean(abs(e) for e in errs), 1),
                        "mean_signed_error": round(statistics.mean(errs), 1),
                        "biggest_misses": sorted(polls, key=lambda p: -abs(p["error"]))[:5]}

    # markets
    mk = (bundle.get("markets") or {})
    h, se = snap.get("house", {}), snap.get("senate", {})
    house_ctrl = "D" if h.get("won", {}).get("D", 0) >= 218 else "R" if h.get("won", {}).get("R", 0) >= 218 else None
    ss = se.get("seats_secured") or {}
    senate_ctrl = "D" if ss.get("D", 0) >= 51 else "R" if ss.get("R", 0) >= 50 else None  # R holds the tie-breaking vice presidency
    markets = {"house": {"final_price_D": (mk.get("house") or {}).get("D"), "control_decided": house_ctrl},
               "senate": {"final_price_D": (mk.get("senate") or {}).get("D"), "control_decided": senate_ctrl},
               "source": "Polymarket, final pre-election price recorded by the tracker"}

    comp = [k for k, r in races.items() if r.get("final_score") is not None and abs(r["final_score"]) <= 2]
    comp_called = [k for k in comp if races[k].get("called")]
    share = round(100 * len(comp_called) / len(comp), 1) if comp else 0.0
    out = {"run_date": run_date, "results_snapshot": os.path.basename(snaps[-1]), "ratings_frozen_from": fz,
           "status": "ready" if share >= 90 else "preliminary", "competitive_called_pct": share,
           "forecasters": table, "consensus": cons, "tossups_won": tossup_wins, "by_rating": by_rating, "upsets": upsets,
           "projections": projections, "governors": gov_score,
           "polls": poll_summary, "markets": markets,
           "caveat": "Vote shares are from the Wikipedia results table and change until counting ends; accuracy counts only races already called."}
    dump(out, os.path.join(ROOT, "docs", "data", "scorecard.json"))

    path = os.path.join(ROOT, "data", "bundle", f"{run_date}.md")
    if os.path.exists(path):
        text = re.split(r"\n### Scorecard", open(path, encoding="utf-8").read())[0].rstrip() + "\n"
        L = [f"\n### Scorecard ({out['status']}; {share}% of the {len(comp)} races rated Lean D to Lean R are called)"]
        L.append(f"- Consensus of forecasters: {cons['right']} right, {cons['wrong']} wrong, {cons['tossup']} rated Tossup"
                 + (f"; misses: {', '.join(cons['misses'][:8])}" if cons["misses"] else "") + f". Tossups won: D {tossup_wins['D']}, R {tossup_wins['R']}.")
        for t in table:
            L.append(f"- {t['forecaster']}: {t['right']} right, {t['wrong']} wrong, {t['tossups']} tossups"
                     + (f" ({t['accuracy_pct']}% of non-tossup calls)" if t["accuracy_pct"] is not None else ""))
        if gov_score["right"] + gov_score["wrong"] + gov_score["tossup"]:
            L.append(f"- Governors (consensus of the forecasters in the gubernatorial article's ratings table): {gov_score['right']} right, {gov_score['wrong']} wrong, "
                     f"{gov_score['tossup']} rated Tossup, {gov_score['uncalled']} not called"
                     + (f"; upsets: {', '.join(u['key'] + ' (' + u['final_rating'] + ', ' + u['party'] + ' won)' for u in gov_score['upsets'])}" if gov_score["upsets"] else "") + ".")
        if poll_summary:
            L.append(f"- Polls (tracked races, final average vs table margin, {poll_summary['races']} races): average miss {poll_summary['mean_abs_error']} pts; "
                     f"signed error {poll_summary['mean_signed_error']:+} (positive = polls underestimated Republicans). Biggest: "
                     + "; ".join(f"{p['key']} avg {p['poll_avg']:+} vs actual {p['actual']:+}" for p in poll_summary["biggest_misses"]))
        else:
            L.append("- Polls: not enough called races with vote shares yet.")
        L.append(f"- Markets: final Polymarket price for Democratic control was {markets['house']['final_price_D']}% (House) and "
                 f"{markets['senate']['final_price_D']}% (Senate); control decided so far: House {house_ctrl or 'not yet'}, Senate {senate_ctrl or 'not yet'}.")
        L.append(f"- Caveat: {out['caveat']} Full table: {CFG.get('site_url', 'https://jakeworcester-byte.github.io/political-tracker/')}#results")
        open(path, "w", encoding="utf-8").write(text + "\n".join(L) + "\n")
    print(f"scorecard: {out['status']}, {share}% of competitive races called, {len(table)} forecasters scored", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
