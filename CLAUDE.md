# Jake's Political Tracker

A daily, automated intelligence feed on the 2026 midterms for Jake Worcester. A cloud Routine runs every morning in a persistent runner session: collect, analyze, write the brief, publish the site, update Notion. A second, tiny Routine then pushes the published brief to Jake's phone. See `ROUTINE_PROMPT.md` for how they are wired. This repository is the system of record. Notion is a reading layer so any Claude chat on Jake's phone or desktop can answer questions from current data.

**Firm rules.** Not a forecast: synthesize what forecasters, polls, markets, and reporting say; never predict. Every claim in a brief carries a source URL. Paraphrase only; never store or reproduce paywalled text. Never bypass a bot challenge, paywall, or terms of service. If a source fails, say so. Read `config/rubric.md` before writing anything for Jake.

## Layout

```
config/sources.json     every source with a lean tag and an enabled flag; feeds, APIs, Google News template
config/candidates.json  party map for candidates the analyzer cannot resolve from the incumbent (open seats)
config/early_vote.json  early vote season, research states, registration baselines with sources
config/rubric.md        scoring, thresholds, guardrails, brief shape (versioned; the routine reads it)
scripts/collect.py      fetch everything -> data/raw/<date>/*.json + coverage.json   (stdlib only)
scripts/analyze.py      raw -> docs/data/latest.json, history.json, races/*.json, data/bundle/<date>.md|json
scripts/early_vote.py   NC daily report + routine research -> docs/data/early_vote.json + Early vote digest section
scripts/publish.py      data/briefs/<date>.md -> docs/data/latest.json brief + docs/data/briefs/
data/raw/<date>/        raw pulls, kept for diagnosis
data/bundle/<date>.md   the digest Claude reads before writing the brief (movers, tracked races, signals by lean)
data/briefs/<date>.md   the brief Claude writes
docs/                   the GitHub Pages site (index.html + assets/ + data/). A workflow mirrors it to the gh-pages branch, which Pages serves
.github/workflows/      pages.yml mirrors docs/ to gh-pages on push to main; deadman.yml opens an issue if no brief landed by 7:20 CT
ROUTINE_PROMPT.md       the exact prompt the cloud Routine runs, version-controlled
```

## Daily pipeline (what the Routine does)

```bash
python3 scripts/collect.py            # ~2-4 min; exit 2 if either ratings table failed (core failure)
python3 scripts/analyze.py            # instant; prints movers/tracked/unresolved counts
# Claude researches other states' early vote -> data/early_vote/research/<date>.json (through Election Day)
python3 scripts/early_vote.py         # NC auto-fetch + merge; appends the Early vote section to the digest
# Claude reads data/bundle/<date>.md and config/rubric.md, writes data/briefs/<date>.md
python3 scripts/publish.py            # validates the brief, folds it into docs/data
git add -A && git commit -m "Daily update <date>" && git push origin HEAD:main
# Then: Notion writes via the connector (Daily Briefs page, Signals cited in the brief, changed Races rows, Inbox processing)
# Finally: the brief itself is the final message, so the Routine's push notification carries it.
```

Dates are America/Chicago. A rerun on the same date overwrites that date's raw and bundle files and compares against the prior run's snapshot, so reruns are idempotent.

## Data model notes

- Race keys: `SEN-ME`, `SEN-OH-S` (special), `HOU-PA-07`, `HOU-AK-AL`.
- Ratings scale: Solid D, Likely D, Lean D, Tilt D, Tossup, Tilt R, Lean R, Likely R, Solid R ("Safe" normalizes to "Solid"). Scores -4..4.
- Consensus = median of forecaster scores, ties toward Tossup. Tracked = consensus within Lean D..Lean R (Jake's rule). Disagreement = spread >= 4 steps.
- Poll margins are R minus D (negative = Democratic lead). Labels render as `D+3.5`.
- Movement thresholds live in `config/rubric.md` and are implemented in `analyze.py::detect_changes`.

## Notion (reading layer)

Parent page "Midterms 2026": https://app.notion.com/p/3f63eda98b27817492f1f2b157c306b5

| Database | Data source | Purpose |
| --- | --- | --- |
| Races | `collection://43141d93-e8c4-4910-ac41-3fa43e1a1fc2` | one row per tracked race, updated in place (match on `Key`) |
| Daily Briefs | `collection://6d2f3fc5-67aa-4761-92f3-1ce274d43f98` | one page per day; body = full brief; query by `Date` before creating |
| Signals | `collection://e4e75cb8-ed2e-4929-ae2e-fd68f432ca4b` | only the signals cited in that day's brief (10 to 20 rows) |
| Inbox | `collection://57747927-9f76-4d52-8565-e7d42a78f56c` | Jake's reading inbox; process rows where Processed is unchecked |

Keep Notion writes small and **create-only** (new Daily Briefs page, new Signals rows). Update, delete, and move calls raise an approval prompt that blocks the unattended runner session, so the routine never issues them; the Races rows are a seeded baseline and the current race state lives in each day's brief page, on the site, and in the raw JSON.

## Conventions

- Python 3.11+, standard library only in `scripts/` so a fresh cloud VM needs no installs.
- The site is plain HTML/CSS/JS with hash routing; no build step. Keep it readable on a phone.
- Commit data changes with the message `Daily update YYYY-MM-DD`. Code changes get normal messages.
- Do not add Jake's Inbox takeaways to the public site unless the row's `Public` box is checked.
