# Jake's Political Tracker

A daily, automated intelligence feed on the 2026 midterms. Every morning a cloud routine collects forecaster ratings, public polls, prediction markets, and reporting across the political spectrum, writes a two-minute brief, publishes a public site, mirrors the essentials to Notion, and pushes the brief to Jake's phone.

- **Site:** https://jakeworcester-byte.github.io/political-tracker/
- **Notion reading layer:** the "Midterms 2026" page (Races, Daily Briefs, Signals, Inbox)
- **Not a forecast.** The tracker synthesizes what credible forecasters, polls, markets, and reporting say. It never predicts.

See `CLAUDE.md` for the full layout and daily pipeline, `config/rubric.md` for the rules, and `ROUTINE_PROMPT.md` for the exact routine prompt.

## Run it by hand

```bash
python3 scripts/collect.py      # fetch everything into data/raw/<date>/
python3 scripts/analyze.py      # build docs/data and data/bundle/<date>.md
# write data/briefs/<date>.md (Claude does this in the routine)
python3 scripts/publish.py      # fold the brief into the site data
```

Standard library only; no installs needed.

## Hosting

The site is served by GitHub Pages from the `gh-pages` branch, which the `Publish site to gh-pages` workflow mirrors from `docs/` on every push to `main`. No Pages settings are required. Optional tidiness: Settings → General → Default branch: **main** (the dead man's switch already runs from the current default branch, which is kept identical to `main`).

## Reliability

- `.github/workflows/pages.yml` mirrors `docs/` to the `gh-pages` branch on every push to `main`, which GitHub Pages serves.
- `.github/workflows/deadman.yml` runs at 7:20 a.m. Central and opens an issue (which emails the owner) if no brief landed that day.
- Every run writes a coverage report; failed sources are named in the brief rather than worked around.
