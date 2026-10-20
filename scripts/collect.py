#!/usr/bin/env python3
"""Collect raw signals for Jake's Political Tracker.

Standard library only, so it runs in any fresh cloud session with no installs.

Sources (all legitimate, machine-readable, one request per source per run):
  * Wikipedia ratings tables for the 2026 Senate and House races. These compile
    Cook, Sabato, Inside Elections, DDHQ, The Economist, Split Ticket, Fox, RCP,
    Silver Bulletin and FiftyPlusOne with the date of each forecaster's update.
  * VoteHub public polls API (generic ballot, approval, Senate, House, governor).
  * Polymarket Gamma API (chamber control and balance-of-power markets).
  * Kalshi public market API (best effort; only priced markets are kept).
  * RSS feeds listed in config/sources.json with lean tags.
  * Google News RSS searches, one per tracked race, for race-specific reporting.

Writes everything to data/raw/<date>/ plus a coverage report. Never bypasses a
bot challenge, paywall, or terms of service: a 403 is recorded as a failure.

Usage: python3 scripts/collect.py [--date YYYY-MM-DD] [--skip google_news,kalshi]
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(ROOT, "config", "sources.json"), encoding="utf-8"))
UA = CONFIG.get("user_agent", "JakesPoliticalTracker/0.1")

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
STATE_BY_ABBR = {v: k for k, v in STATES.items()}

# Forecaster column abbreviations as they appear in the Wikipedia headers.
FORECASTERS = {
    "cook": "Cook Political Report", "ddhq": "Decision Desk HQ", "econ": "The Economist",
    "econ.": "The Economist", "fpo": "FiftyPlusOne", "fox": "Fox News Power Rankings",
    "ie": "Inside Elections", "rcp": "RealClearPolling", "sabato": "Sabato's Crystal Ball",
    "silver": "Silver Bulletin", "st": "Split Ticket", "nbc": "NBC News",
}


class Coverage:
    def __init__(self):
        self.items = []

    def add(self, source_id, name, ok, detail="", count=0, status=None, seconds=0.0):
        self.items.append({
            "id": source_id, "name": name, "ok": bool(ok), "detail": detail,
            "count": count, "status": status, "seconds": round(seconds, 2),
        })
        flag = "ok " if ok else "FAIL"
        print(f"[{flag}] {name}: {detail or count}", file=sys.stderr)

    def summary(self):
        attempted = len(self.items)
        succeeded = sum(1 for i in self.items if i["ok"])
        return {
            "attempted": attempted, "succeeded": succeeded, "failed": attempted - succeeded,
            "failed_sources": [i["name"] for i in self.items if not i["ok"]],
            "items": self.items,
        }


def fetch(url, timeout=45, accept=None, retries=1):
    """GET a URL. Returns (status, bytes). Raises on network failure after retries."""
    headers = {"User-Agent": UA, "Accept": accept or "*/*", "Accept-Language": "en-US,en;q=0.8"}
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            # 4xx is a definitive answer; don't hammer the server.
            return e.code, e.read() if e.fp else b""
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {url}: {last_err}")


# --------------------------------------------------------------------------- Wikipedia

class TableParser(HTMLParser):
    """Collects top-level tables as rows of cells with text, rowspan, colspan."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.depth, self.cur, self.row, self.cell = [], 0, None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.cur = {"class": a.get("class", ""), "rows": []}
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("td", "th"):
            self.cell = {"tag": tag, "text": "", "rowspan": int(a.get("rowspan") or 1),
                         "colspan": int(a.get("colspan") or 1)}
        elif self.cell is not None and tag == "br":
            self.cell["text"] += " "
        elif self.cell is not None and tag == "sup":
            self.cell["text"] += "\x00"  # mark footnote start

    def handle_endtag(self, tag):
        if tag == "table":
            if self.depth == 1 and self.cur:
                self.tables.append(self.cur)
                self.cur = None
            self.depth -= 1
        elif self.depth == 1 and tag == "tr" and self.row is not None:
            if self.cur is not None:
                self.cur["rows"].append(self.row)
            self.row = None
        elif self.depth == 1 and tag in ("td", "th") and self.cell is not None:
            text = re.sub(r"\x00[^\x00\x01]*\x01", "", self.cell["text"])  # strip footnotes
            self.cell["text"] = re.sub(r"\s+", " ", text).strip()
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        elif self.cell is not None and tag == "sup":
            self.cell["text"] += "\x01"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


RATING_RE = re.compile(r"^(Safe|Solid|Likely|Lean|Tilt)\s+([DR])(?:\s*\(flip\))?$|^Toss-?up$", re.I)


def normalize_rating(text):
    t = text.replace("Toss-up", "Tossup").replace("Toss up", "Tossup").strip()
    flip = "(flip)" in t
    t = t.replace("(flip)", "").strip()
    m = re.match(r"^(Safe|Solid|Likely|Lean|Tilt)\s+([DR])$", t, re.I)
    if m:
        word = m.group(1).title()
        if word == "Safe":
            word = "Solid"
        return f"{word} {m.group(2).upper()}", flip
    if t.lower() == "tossup":
        return "Tossup", flip
    return None, flip


def parse_header_forecasters(header_cells):
    """Header like 'CookOct 6,2026' or 'Econ.Oct. 7,2026' -> (key, name, date)."""
    out = []
    for c in header_cells:
        txt = c["text"]
        m = re.match(r"^([A-Za-z.]+?)\s*((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{1,2},?\s*\d{4})", txt)
        if not m:
            out.append(None)
            continue
        abbr = m.group(1).strip().lower()
        name = FORECASTERS.get(abbr, FORECASTERS.get(abbr.rstrip("."), abbr))
        datestr = re.sub(r"\.", "", m.group(2))
        date = None
        for fmt in ("%b %d, %Y", "%b %d,%Y", "%b %d %Y", "%B %d, %Y"):
            try:
                date = dt.datetime.strptime(re.sub(r"\s+", " ", datestr).replace(" ,", ","), fmt).date().isoformat()
                break
            except ValueError:
                continue
        out.append({"abbr": abbr.rstrip("."), "name": name, "as_of": date})
    return out


def find_ratings_table(tables):
    best = None
    for t in tables:
        txt = " ".join(c["text"] for r in t["rows"][:3] for c in r)
        if "Ratings" in txt and ("Cook" in txt or "Sabato" in txt):
            body = " ".join(c["text"] for r in t["rows"] for c in r)
            if "Tossup" in body or "Toss-up" in body or "Lean" in body:
                if best is None or len(t["rows"]) > len(best["rows"]):
                    best = t
    return best


def parse_ratings_table(html, chamber):
    p = TableParser()
    p.feed(html)
    table = find_ratings_table(p.tables)
    if not table:
        raise RuntimeError("ratings table not found")
    rows = table["rows"]
    # Second header row holds the per-column labels.
    header = rows[1] if len(rows) > 1 else rows[0]
    labels = [c["text"] for c in header]
    # Find where ratings columns start: first header cell with a date in it.
    fc = parse_header_forecasters(header)
    first_rating_col = next((i for i, f in enumerate(fc) if f), None)
    if first_rating_col is None:
        raise RuntimeError("no forecaster columns detected")
    forecasters = [f for f in fc[first_rating_col:] if f]
    races, overall = [], {}
    for r in rows[2:]:
        cells = [c["text"] for c in r]
        if not cells:
            continue
        label = cells[0]
        if label.lower().startswith("overall"):
            for f, cell in zip(forecasters, cells[1:]):
                m = re.search(r"D\s*[–-]\s*(\d+)\s*R\s*[–-]\s*(\d+)\s*(\d+)?", cell)
                if m:
                    overall[f["abbr"]] = {"name": f["name"], "D": int(m.group(1)), "R": int(m.group(2)),
                                          "tossups": int(m.group(3)) if m.group(3) else None, "as_of": f["as_of"]}
            continue
        if len(cells) < first_rating_col + 1:
            continue
        ratings, flips = {}, 0
        for f, cell in zip(forecasters, cells[first_rating_col:]):
            rating, flip = normalize_rating(cell)
            if rating:
                ratings[f["abbr"]] = {"rating": rating, "flip": flip, "as_of": f["as_of"], "name": f["name"]}
                flips += 1 if flip else 0
        if not ratings:
            continue
        race = {"chamber": chamber, "label": label, "ratings": ratings}
        # Standard preceding columns: PVI, incumbent, last result (Senate adds 'senator').
        race["pvi"] = cells[1] if len(cells) > 1 else ""
        race["incumbent"] = cells[2] if len(cells) > 2 else ""
        race["last_result"] = cells[3] if len(cells) > 3 else ""
        m = re.search(r"([\d.]+)%\s*([DRI])", race["last_result"])
        inc_party = m.group(2) if m else None
        if not inc_party:
            # Infer from ratings: a '(flip)' marks the other party as the incumbent.
            votes = {}
            for v in ratings.values():
                side = v["rating"][-1] if v["rating"] != "Tossup" else None
                if side:
                    party = ("D" if side == "R" else "R") if v["flip"] else side
                    votes[party] = votes.get(party, 0) + 1
            inc_party = max(votes, key=votes.get) if votes else None
        race["incumbent_party"] = inc_party
        race["open_seat"] = bool(re.search(r"retir|resign|appoint|vacant|running for|term-limited", race["incumbent"], re.I))
        race["incumbent"] = re.sub(r"\s*\((retiring|resigning|running for.*?|appointed.*?)\)\s*", "", race["incumbent"]).strip()
        # Keys
        if chamber == "Senate":
            special = "special" in label.lower()
            state = re.sub(r"\s*\(.*?\)\s*", "", label).strip()
            abbr = STATES.get(state)
            if not abbr:
                continue
            race["state"] = state
            race["state_abbr"] = abbr
            race["special"] = special
            race["district"] = None
            race["key"] = f"SEN-{abbr}" + ("-S" if special else "")
            race["name"] = f"{state} Senate" + (" (special)" if special else "")
        else:
            m = re.match(r"^(.*?)\s+(\d+|at-large)$", label, re.I)
            if not m:
                continue
            state, dist = m.group(1).strip(), m.group(2)
            abbr = STATES.get(state)
            if not abbr:
                continue
            dnum = "AL" if dist.lower() == "at-large" else f"{int(dist):02d}"
            race["state"] = state
            race["state_abbr"] = abbr
            race["special"] = False
            race["district"] = dnum
            race["key"] = f"HOU-{abbr}-{dnum}"
            race["name"] = f"{abbr}-{dnum}"
        races.append(race)
    return {"forecasters": forecasters, "races": races, "overall": overall}


def collect_wikipedia(cov, raw_dir, which):
    src = next(s for s in CONFIG["structured"] if s["id"] == f"wiki_{which}_ratings")
    chamber = "Senate" if which == "senate" else "House"
    t0 = time.time()
    cache_dir = os.environ.get("TRACKER_WIKI_CACHE")
    html = None
    if cache_dir:
        slug = src["url"].rsplit("/", 1)[-1]
        for cand in (f"{slug}.html", f"{which}_page.html", f"{which}_ratings.html"):
            path = os.path.join(cache_dir, cand)
            if os.path.exists(path):
                html = open(path, encoding="utf-8").read()
                break
    status = 200 if html else None
    try:
        if html is None:
            status, body = fetch(src["url"], accept="text/html")
            if status != 200:
                cov.add(src["id"], src["name"], False, f"HTTP {status}", status=status, seconds=time.time() - t0)
                return None
            html = body.decode("utf-8", "replace")
        data = parse_ratings_table(html, chamber)
        data["source_url"] = src["url"]
        data["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        json.dump(data, open(os.path.join(raw_dir, f"ratings_{which}.json"), "w", encoding="utf-8"), indent=1)
        cov.add(src["id"], src["name"], True, f"{len(data['races'])} races, {len(data['forecasters'])} forecasters",
                count=len(data["races"]), status=status, seconds=time.time() - t0)
        return data
    except Exception as e:  # noqa: BLE001
        cov.add(src["id"], src["name"], False, f"{type(e).__name__}: {e}", status=status, seconds=time.time() - t0)
        return None


# --------------------------------------------------------------------------- VoteHub

def collect_votehub(cov, raw_dir, since):
    src = next(s for s in CONFIG["structured"] if s["id"] == "votehub_polls")
    t0 = time.time()
    try:
        status, body = fetch(src["url"], accept="application/json", timeout=90)
        if status != 200:
            cov.add(src["id"], src["name"], False, f"HTTP {status}", status=status, seconds=time.time() - t0)
            return None
        polls = json.loads(body.decode("utf-8"))
        keep = []
        for p in polls:
            if (p.get("end_date") or "") < since:
                continue
            if p.get("poll_type") not in ("generic-ballot", "approval", "us-senator", "us-representative", "governor"):
                continue
            if p.get("poll_type") == "approval" and (p.get("subject") or "").lower() != "donald trump":
                continue
            keep.append(p)
        out = {"source_url": src["url"], "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "since": since, "total_in_feed": len(polls), "polls": keep}
        json.dump(out, open(os.path.join(raw_dir, "votehub.json"), "w", encoding="utf-8"), indent=1)
        cov.add(src["id"], src["name"], True, f"{len(keep)} polls since {since} (of {len(polls)})", count=len(keep),
                status=status, seconds=time.time() - t0)
        return out
    except Exception as e:  # noqa: BLE001
        cov.add(src["id"], src["name"], False, f"{type(e).__name__}: {e}", seconds=time.time() - t0)
        return None


# --------------------------------------------------------------------------- Markets

def collect_polymarket(cov, raw_dir):
    src = next(s for s in CONFIG["structured"] if s["id"] == "polymarket")
    t0 = time.time()
    events, errors = [], []
    for slug in src["events"]:
        try:
            status, body = fetch(f"{src['url']}?slug={urllib.parse.quote(slug)}", accept="application/json")
            if status != 200:
                errors.append(f"{slug}: HTTP {status}")
                continue
            arr = json.loads(body.decode("utf-8"))
            if not arr:
                errors.append(f"{slug}: empty")
                continue
            e = arr[0]
            markets = []
            for m in e.get("markets", []):
                try:
                    outcomes = json.loads(m.get("outcomes") or "[]")
                    prices = json.loads(m.get("outcomePrices") or "[]")
                except Exception:  # noqa: BLE001
                    outcomes, prices = [], []
                if not prices:
                    continue
                markets.append({
                    "question": m.get("question"), "slug": m.get("slug"),
                    "outcomes": outcomes, "prices": [float(x) for x in prices],
                    "volume": float(m.get("volume") or 0), "liquidity": float(m.get("liquidity") or 0),
                    "end_date": m.get("endDate"), "url": f"https://polymarket.com/event/{slug}",
                })
            events.append({"slug": slug, "title": e.get("title"), "url": f"https://polymarket.com/event/{slug}",
                           "volume": float(e.get("volume") or 0), "markets": markets})
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{slug}: {type(ex).__name__}: {ex}")
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "events": events, "errors": errors}
    json.dump(out, open(os.path.join(raw_dir, "polymarket.json"), "w", encoding="utf-8"), indent=1)
    ok = len(events) > 0
    cov.add(src["id"], src["name"], ok, f"{len(events)}/{len(src['events'])} events" + (f"; {errors}" if errors else ""),
            count=len(events), seconds=time.time() - t0)
    return out if ok else None


def collect_kalshi(cov, raw_dir):
    src = next(s for s in CONFIG["structured"] if s["id"] == "kalshi")
    t0 = time.time()
    markets, errors = [], []
    for series in src.get("series", []):
        try:
            status, body = fetch(f"{src['url']}/markets?series_ticker={series}&limit=50", accept="application/json")
            if status != 200:
                errors.append(f"{series}: HTTP {status}")
                continue
            for m in json.loads(body.decode("utf-8")).get("markets", []):
                price = m.get("last_price")
                if price is None and m.get("yes_bid") is not None and m.get("yes_ask") is not None:
                    price = (m["yes_bid"] + m["yes_ask"]) / 2
                if price is None:
                    continue
                markets.append({"series": series, "ticker": m.get("ticker"), "title": m.get("title"),
                                "yes_price": price, "volume": m.get("volume"), "status": m.get("status"),
                                "close_time": m.get("close_time"),
                                "url": f"https://kalshi.com/markets/{series.lower()}"})
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{series}: {type(ex).__name__}: {ex}")
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "markets": markets, "errors": errors}
    json.dump(out, open(os.path.join(raw_dir, "kalshi.json"), "w", encoding="utf-8"), indent=1)
    ok = len(markets) > 0 or src.get("optional", False)
    cov.add(src["id"], src["name"], ok, f"{len(markets)} priced markets" + (f"; {errors}" if errors else "")
            + ("" if markets else " (optional source, no priced markets today)"), count=len(markets), seconds=time.time() - t0)
    return out


# --------------------------------------------------------------------------- RSS

def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        pass
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def parse_feed(body):
    root = ET.fromstring(body)
    items = []
    ns = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/",
          "dc": "http://purl.org/dc/elements/1.1/", "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel_title = root.findtext("./channel/title") or root.findtext("atom:title", namespaces=ns) or ""
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            g = it.findtext("guid") or ""
            link = g if g.startswith("http") else ""
        desc = it.findtext("description") or it.findtext("itunes:summary", namespaces=ns) or ""
        items.append({
            "title": strip_html(it.findtext("title") or ""), "link": link,
            "published": parse_date(it.findtext("pubDate") or it.findtext("dc:date", namespaces=ns)),
            "summary": strip_html(desc)[:600], "source_title": strip_html(it.findtext("source") or ""),
        })
    if not items:  # Atom
        for e in root.findall("atom:entry", ns):
            link_el = e.find("atom:link", ns)
            items.append({
                "title": strip_html(e.findtext("atom:title", namespaces=ns) or ""),
                "link": link_el.get("href") if link_el is not None else "",
                "published": parse_date(e.findtext("atom:published", namespaces=ns) or e.findtext("atom:updated", namespaces=ns)),
                "summary": strip_html(e.findtext("atom:summary", namespaces=ns) or e.findtext("atom:content", namespaces=ns) or "")[:600],
                "source_title": "",
            })
    return strip_html(channel_title), items


def is_relevant(text):
    t = text.lower()
    return any(k in t for k in CONFIG["keywords"]["midterm_relevance"])


def collect_feeds(cov, raw_dir, now):
    out = {"fetched_at": now.isoformat(), "feeds": []}
    for f in CONFIG["feeds"]:
        if not f.get("enabled", True):
            continue
        t0 = time.time()
        window_days = 7 if f["kind"] == "podcast" else 3
        cutoff = (now - dt.timedelta(days=window_days)).isoformat()
        try:
            status, body = fetch(f["url"], accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
            if status != 200:
                cov.add(f["id"], f["name"], False, f"HTTP {status}", status=status, seconds=time.time() - t0)
                continue
            title, items = parse_feed(body)
            kept = []
            for it in items:
                if it["published"] and it["published"] < cutoff:
                    continue
                relevant = is_relevant(it["title"] + " " + it["summary"])
                if f["kind"] != "podcast" and not relevant:
                    continue
                it["relevant"] = relevant
                kept.append(it)
            kept = kept[:40]
            out["feeds"].append({"id": f["id"], "name": f["name"], "lean": f["lean"], "kind": f["kind"],
                                 "url": f["url"], "channel_title": title, "items": kept, "total_items": len(items)})
            cov.add(f["id"], f["name"], True, f"{len(kept)} relevant of {len(items)} in last {window_days}d",
                    count=len(kept), status=status, seconds=time.time() - t0)
        except Exception as e:  # noqa: BLE001
            cov.add(f["id"], f["name"], False, f"{type(e).__name__}: {e}", seconds=time.time() - t0)
    json.dump(out, open(os.path.join(raw_dir, "feeds.json"), "w", encoding="utf-8"), indent=1)
    return out


# --------------------------------------------------------------------------- Google News

RATING_SCORE = {"Solid D": -4, "Likely D": -3, "Lean D": -2, "Tilt D": -1, "Tossup": 0,
                "Tilt R": 1, "Lean R": 2, "Likely R": 3, "Solid R": 4}


def quick_tracked(ratings_data):
    """Races whose median forecaster rating is Lean D..Lean R (Jake's tracked band)."""
    tracked = []
    for r in ratings_data.get("races", []):
        scores = sorted(RATING_SCORE[v["rating"]] for v in r["ratings"].values())
        if not scores:
            continue
        mid = scores[len(scores) // 2] if len(scores) % 2 else scores[len(scores) // 2 - 1: len(scores) // 2 + 1]
        median = mid if isinstance(mid, int) else (mid[0] if abs(mid[0]) < abs(mid[1]) else mid[1])
        if -2 <= median <= 2:
            tracked.append(r)
    return tracked


def race_query(race):
    if race["chamber"] == "Senate":
        surname = race["incumbent"].split()[-1] if race["incumbent"] else ""
        q = f'"{race["state"]}" Senate race 2026'
        if surname and not race.get("open_seat"):
            q += f" {surname}"
        return q
    dist = race["district"]
    if dist == "AL":
        return f'"{race["state"]}" at-large congressional race 2026'
    ordinal = f"{int(dist)}{'th' if 11 <= int(dist) % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(int(dist) % 10, 'th')}"
    return f'"{race["state"]}" "{ordinal} congressional district" 2026'


def collect_google_news(cov, raw_dir, now, tracked_races):
    gn = CONFIG["google_news"]
    if not gn.get("enabled", True):
        return None
    t0 = time.time()
    cutoff = (now - dt.timedelta(days=5)).isoformat()
    out = {"fetched_at": now.isoformat(), "races": {}, "national": {}, "errors": []}
    queries = [(r["key"], race_query(r)) for r in tracked_races] + [(f"NATIONAL-{i}", q) for i, q in enumerate(gn.get("national_queries", []))]
    for key, q in queries:
        url = gn["template"].format(query=urllib.parse.quote(q))
        try:
            status, body = fetch(url, accept="application/rss+xml, application/xml, text/xml", timeout=30)
            if status != 200:
                out["errors"].append(f"{key}: HTTP {status}")
                continue
            _, items = parse_feed(body)
            kept = []
            for it in items:
                if it["published"] and it["published"] < cutoff:
                    continue
                # Google News titles end with " - Outlet"; keep the outlet.
                m = re.match(r"^(.*)\s-\s([^-]+)$", it["title"])
                outlet = it["source_title"] or (m.group(2).strip() if m else "")
                kept.append({"title": m.group(1).strip() if m else it["title"], "outlet": outlet,
                             "link": it["link"], "published": it["published"]})
                if len(kept) >= gn.get("max_items_per_race", 8):
                    break
            target = out["national"] if key.startswith("NATIONAL-") else out["races"]
            target[key] = {"query": q, "items": kept}
            time.sleep(0.4)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{key}: {type(e).__name__}: {e}")
    json.dump(out, open(os.path.join(raw_dir, "google_news.json"), "w", encoding="utf-8"), indent=1)
    n_items = sum(len(v["items"]) for v in out["races"].values()) + sum(len(v["items"]) for v in out["national"].values())
    ok = len(out["races"]) + len(out["national"]) > 0
    cov.add("google_news", "Google News race searches", ok,
            f"{len(out['races'])} races + {len(out['national'])} national queries, {n_items} items"
            + (f"; {len(out['errors'])} errors" if out["errors"] else ""), count=n_items, seconds=time.time() - t0)
    return out


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Run date YYYY-MM-DD (default: today, America/Chicago)")
    ap.add_argument("--skip", default="", help="Comma list of collectors to skip: wikipedia,votehub,polymarket,kalshi,feeds,google_news")
    args = ap.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    now = dt.datetime.now(dt.timezone.utc)
    central = now.astimezone(dt.timezone(dt.timedelta(hours=-5)))  # CDT; the date only matters for file naming
    run_date = args.date or central.date().isoformat()
    raw_dir = os.path.join(ROOT, "data", "raw", run_date)
    os.makedirs(raw_dir, exist_ok=True)
    cov = Coverage()
    print(f"collecting for {run_date} into {raw_dir}", file=sys.stderr)

    senate = house = None
    if "wikipedia" not in skip:
        senate = collect_wikipedia(cov, raw_dir, "senate")
        time.sleep(3)  # be polite to Wikipedia
        house = collect_wikipedia(cov, raw_dir, "house")
    if "votehub" not in skip:
        since = (now - dt.timedelta(days=120)).date().isoformat()
        collect_votehub(cov, raw_dir, since)
    if "polymarket" not in skip:
        collect_polymarket(cov, raw_dir)
    if "kalshi" not in skip:
        collect_kalshi(cov, raw_dir)
    if "feeds" not in skip:
        collect_feeds(cov, raw_dir, now)
    if "google_news" not in skip:
        tracked = []
        for data in (senate, house):
            if data:
                tracked.extend(quick_tracked(data))
        collect_google_news(cov, raw_dir, now, tracked)

    summary = cov.summary()
    summary["run_date"] = run_date
    summary["started_at"] = now.isoformat()
    summary["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    json.dump(summary, open(os.path.join(raw_dir, "coverage.json"), "w", encoding="utf-8"), indent=1)
    print(f"\ncoverage: {summary['succeeded']}/{summary['attempted']} sources ok; failed: {summary['failed_sources']}", file=sys.stderr)
    core_ok = senate is not None and house is not None
    return 0 if core_ok else 2


if __name__ == "__main__":
    sys.exit(main())
