# Routine prompt: Midterm Tracker morning run

This is the authoritative instruction set the cloud Routine follows every morning. Keep it in sync with the Routines at https://claude.ai/code/routines. Version: 2026-11-01a (post-election mode from 2026-11-04 with an evening edition through 2026-11-06; see the last section).

## How the routines are wired

Two routines, because a fresh routine session has no repository credential and no connectors, while a persistent session keeps both:

1. **Midterm Tracker morning run** (5:07 a.m. Central) fires into the persistent runner session `session_01Ck3jX417QeWHBSEtBAA7PR` (model: Claude Opus 5.5, Jake's choice), which was created with the repository as its source (push access to `main`) and carries the Notion connector. It does everything below. An earlier runner, `session_01DfgwjsFRTn4HqAT89fuSzh`, ran the verification tests on 2026-10-11 and is retired.
2. **Midterm Tracker brief to phone** (5:47 a.m. Central) is a fresh-session routine with push and email notifications on. It only fetches `docs/data/briefs/<today>.md` from raw GitHub and outputs it verbatim, so its completion notification on Jake's phone *is* the brief. If no brief is published by then, it notifies Jake of the failure instead.

3. **Midterm Tracker evening run** (4:17 p.m. Central, November 4 to 6 only) fires into the same runner session and follows the post-election section below with `--edition pm`.
4. **Midterm Tracker evening brief to phone** (4:57 p.m. Central, November 4 to 6) is a fresh-session routine like number 2 that fetches `docs/data/briefs/<today>-pm.md`.

If the runner session is ever archived or expires, create a new session with `source_url` = the repository and `outcome_branch` = `main`, then rebind the morning routine to its id (`persistent_session_id`).

---

You are running Jake Worcester's Midterm Tracker morning update inside the repository `jakeworcester-byte/political-tracker`. Work autonomously; nobody is watching. Read `CLAUDE.md` and `config/rubric.md` first and follow them exactly. The whole run should take well under 30 minutes.

Today's date is the current date in America/Chicago.

**On or after 2026-11-04 (`config/postelection.json` → `post_election_from`), skip steps 1 to 6 below and follow "Post-election mode" at the end of this file instead.**

## 1. Collect and analyze

If the repository is not already in your working directory (a fresh routine session may start empty), clone it first: `git clone https://github.com/jakeworcester-byte/political-tracker && cd political-tracker`. If a later push is refused for lack of credentials, call the `add_repo` tool with owner `jakeworcester-byte`, repo `political-tracker`, access `push`, then retry the push.

```bash
git stash push -u -m "leftover before run" || true   # never git clean / checkout -- . / reset --hard: the permission classifier denies those
git checkout main && git pull --ff-only origin main
python3 scripts/collect.py
python3 scripts/analyze.py
```

If `collect.py` exits with code 2, both ratings tables failed. Retry once after 60 seconds. If it fails again, skip to step 6 and write a failure notice instead of a brief.

## 1b. Early vote research (optional supplement, about 5 minutes)

Through Election Day (`config/early_vote.json` → `season.end`), step 1c already pulls every state's early ballots by party from the UF Election Lab's published CSV and North Carolina from the State Board's raw ballot file. Do not re-research those numbers. Research is only for what the feed lacks: a **same-point prior-cycle comparison** (for example a state report or major outlet giving the 2022 figure at the same point) for a `research_states` state. If you find nothing in a few searches, skip this step; a missing research file is fine. Do not try to scrape the tracker page itself or any page behind a bot challenge.

- Use WebSearch and WebFetch. At most about 15 fetches in total. If those tools are not available in this session, write the file below with `"states": []` and one `unavailable` entry saying so, and move on.
- Allowed sources, in order of preference: the state election office's own report or data file; the UF Election Lab tracker (election.lab.ufl.edu); major outlets reporting the official numbers (AP, Reuters, CNN, NBC, ABC, CBS, NYT, WSJ, Washington Post, Politico, or the leading local outlet in that state). Never use social media posts, partisan blogs, or campaign press releases as the source of a number.
- Only record a state when the source gives early ballots (returned mail, in-person early, or both) **by party registration**, with a date. Ballot requests alone do not count; note them under `unavailable` instead. Record a prior-cycle comparison only when the source itself reports the same point in that cycle.
- Write `data/early_vote/research/<today>.json` in exactly this shape (shares are percentages of ballots that sum to about 100; `total`, `prior`, `registration_share` and `note` are optional):

```json
{"date": "<today>",
 "states": [
  {"state": "FL", "as_of": "YYYY-MM-DD", "measure": "returned mail ballots", "total": 412000,
   "ballots_share": {"D": 43.3, "R": 35.6, "Other": 21.1},
   "prior": {"cycle": "2022", "share": {"D": 0, "R": 0, "Other": 0}, "total": 0},
   "source": "Florida Division of Elections", "source_url": "https://...", "note": "one short line if needed"}],
 "unavailable": [{"state": "PA", "reason": "only request shares published"}]}
```

- Registration baselines come from `config/early_vote.json` → `registration`. If a researched state has no baseline, add one only from that state's official election office (D, R, Other percentages, `as_of`, `source`, `source_url`); otherwise leave it out and the site shows the ballots without a gap. Ohio has no party registration; label its affiliation as last-primary-voted and give no registration baseline.

## 1c. Build the early vote panel

```bash
python3 scripts/early_vote.py
```

This fetches North Carolina's daily State Board of Elections report and raw ballot file, the UF Election Lab's state-by-state CSV, merges your research file (which overrides UF for a state), computes each state's gap versus registration and versus the prior cycle, updates `docs/data/early_vote.json` (the site's Early vote tab), and appends an "Early vote" section to today's digest. It never fails the run; problems are listed in the digest.

## 2. Read the digest

Read `data/bundle/<today>.md` in full. It contains: national numbers, chamber overview, exactly what moved since yesterday (numeric), every tracked race with its ratings, polls, and trend, recent signals grouped by source lean, and the coverage report. Also read `data/bundle/<today>.json` for the Notion race rows.

If the digest lists races under "unresolved candidate parties," look at the Google News headlines for those races in `data/raw/<today>/google_news.json`. When a headline makes a candidate's party unambiguous (for example "Democrat Jane Doe" or "(D)"), add the mapping to `config/candidates.json` with the source in `_sources`, then rerun `python3 scripts/analyze.py`. Do not guess.

## 3. Write the brief

Write `data/briefs/<today>.md` following the brief shape in `config/rubric.md`. Hard rules:

- Title line: `# Midterm brief: <Weekday, Month D>`.
- Sections, in order, as `## ` headings: Bottom line; What moved; Races to watch today; Early vote (through Election Day); What the spectrum is saying; Talking points; Coverage note. End with a line linking the site: https://jakeworcester-byte.github.io/political-tracker/
- **Early vote**: two to four lines from the digest's Early vote section. Lead with the change versus the same point in a prior cycle where one exists, then the gap versus registration, each with its source link and as-of date. Always say these are party registrations, not votes. Name a large day-over-day move only if the digest lists it. If no state reported today, write one line saying so. With many states reporting, pick the large states and the states with tracked Senate races (one combined line is fine), and name the original state source when the figure came through the UF Election Lab.
- Under 600 words. Readable in two minutes on a phone.
- Every number carries its source and date; every factual claim is followed by a link in parentheses or Markdown link form. Use the URLs from the digest. No unsourced claims.
- "What moved" uses only the movers the digest lists. If the digest says nothing crossed a threshold, write one line saying so and move on. Do not manufacture drama.
- "What the spectrum is saying" paraphrases at least one left-leaning and one right-leaning source from the digest's signal lists, labeled by lean. If one side has nothing relevant today, say so.
- Never write "I predict," "I expect," or "we think." Write "forecasters rate," "polls show," "markets price."
- Paraphrase only; no quotes longer than a short attributed phrase.

## 4. Publish the site

```bash
python3 scripts/publish.py
git add -A
git commit -m "Daily update <today>"
git push origin HEAD:main
```

Push directly to `main`; do not create a `claude/` branch or a pull request. A workflow mirrors `docs/` to the `gh-pages` branch, which GitHub Pages serves, so the site updates a minute or two after the push. If the push is rejected because `main` moved, run `git pull --rebase origin main` and push again.

## 5. Update Notion (reading layer)

Use the Notion connector tools. Database IDs are in `CLAUDE.md`. **Create-only rule:** call only page-creation tools (`notion-create-pages`). Never call `notion-update-page`, `notion-update-data-source`, delete, or move: in the runner session those calls raise an approval prompt that blocks the whole session until a human clicks, and nobody is watching. Every Notion write below is a create.

1. **Daily Briefs**: query the data source for a page whose Date is today. If none exists, create one titled `Midterm brief <today>` with the full brief as the page body, followed by a `## Tracked races today` section listing every tracked race as one line: key, name, consensus rating, polling average, trend, and the site link (from `notion_race_rows` in the bundle JSON). Properties: Date, Bottom line, Top movers (one line per mover, or "none"), Senate control (markets), House control (markets), Generic ballot, Presidential approval, Coverage (`<ok>/<attempted> sources; failed: ...`), Signals written, Site link, Run status (OK, Degraded if sources failed, Failed otherwise). If a page for today already exists (a same-day rerun), create nothing and move on.
2. **Signals**: query Signals for rows dated today, then create one row per source cited in the brief whose URL is not already present (typically 10 to 20). Fields: Headline, Date, Race key or `National`, Type, Source, Source lean, Quality (per rubric), URL, Summary (your own words, one or two sentences), Why it matters (one line).
3. **Races**: the Races rows are a baseline snapshot seeded on 2026-10-11 and are not updated by the routine (that would need update calls). Current race state lives in the Daily Briefs page's tracked-races section, on the site, and in the raw JSON. When a tracked race's consensus rating changes, also create a Signal of type `rating change` for it so the change is visible in Notion.
4. **Inbox**: query rows where Processed is unchecked. For each whose URL does not already have a Signals row, create a Signal of type `jake inbox`, lean `jake`, Summary = Jake's takeaway (never the article text). Do not try to check Processed (an update call); the URL check prevents duplicates. Mention these in the brief's spectrum section only if they are relevant today.

Keep Notion calls bounded: batch page creations in one call where the tool allows it, and never create more than 60 pages in one run. If any Notion call fails, continue; Notion is a reading layer, and the site plus raw GitHub data are the source of truth.

## 6. Finish

Output the full text of `data/briefs/<today>.md` followed by one line: `Coverage: <ok>/<attempted> sources. Site updated. Notion updated.` (or the honest equivalent if a step failed). The notifier routine delivers the published brief to Jake's phone about 40 minutes later, so the push to `main` is the deliverable.

If the run failed at a core step (no ratings, no brief, or the push failed twice), your final message is instead a short failure notice: what failed, the error text, and what Jake can do (open the run, or press Run now after the fix). Never end silently.

---

## Post-election mode (from 2026-11-04)

The brief becomes an **election update**: actual outcomes, every race still uncalled, recounts and certification, legal and federal action, what exit polls say, and later the lessons-learned scorecard. Read the "Post-election" section of `config/rubric.md` first; its guardrails override anything above. The edition is `am` for the 5:07 a.m. run and `pm` for the 4:17 p.m. run (evening editions run November 4 to 6). Below, `<slug>` is `<today>` for am and `<today>-pm` for pm.

### P1. Get current and gather

```bash
git stash push -u -m "leftover before run" || true
git checkout main && git pull --ff-only origin main
# am edition only, best-effort (the ratings article may change after the election; exit 2 is not a failure now):
python3 scripts/collect.py || true
python3 scripts/analyze.py || true
# both editions, the core step (about 3 minutes; never exits non-zero):
python3 scripts/results.py --edition am      # or --edition pm
python3 scripts/scorecard.py
```

`results.py` reads the House and Senate results tables on Wikipedia (editors mark a race called when AP or the networks call it), compares them with the last snapshot to find new calls, uses the frozen pre-election ratings (the last run before Election Day) to separate competitive races from safe seats, searches Google News for every competitive uncalled race and for national leads (chamber calls, counting, recounts and certification, legal and federal action, exit polls), writes `docs/data/results.json` for the site's Results tab, and appends a "Results (post-election)" section to `data/bundle/<today>.md`. `scorecard.py` appends a "Scorecard" block. Skip `early_vote.py`; early voting is over.

If `results.py` reports that a results table failed to parse, the run still continues: write the update from the news leads, say so plainly in the Coverage note, and list what could not be checked.

### P2. Verify (about 10 to 15 minutes; WebSearch and WebFetch if available, at most about 30 fetches)

- Read the digest's Results section in full.
- **Calls.** For each competitive race in "Called since the last update", confirm the call with a news lead from the digest (AP, a network, or a major outlet saying who called it). If no lead confirms it, write "marked called in the Wikipedia results table" and link the table. For races tagged "HEADLINE SUGGESTS A CALL", open the lead; if it reports an AP or network call, treat the race as called and attribute it; otherwise leave it uncalled.
- **Uncalled.** For each race in "Competitive races not yet called", take the count from the table line and, where a lead gives a fresher count or share counted, use that with its source and time. Do not research every race individually; prioritize Senate races, races tagged close or counting, and races with leads.
- **Recounts, certification, legal and federal action.** Open the relevant national leads. Prefer the state election office, the court, or a major outlet. A partisan or advocacy tracker can point you to a case, but cite the court record or a news report.
- **Exit polls (am edition).** Use AP VoteCast or network exit polls from the leads, labeled as surveys.
- Never get around a bot check or paywall. CourtListener needs an account key we do not have; do not try it.

### P3. Write the update

Write `data/briefs/<slug>.md`:

- Title: `# Election update: <Weekday, Month D> (morning)` or `(evening)`.
- Sections as `## ` headings, in this order (publish.py checks them): **Bottom line**; **Called since the last update**; **Still uncalled**; **Recounts and certification**; **Legal and federal action**; **What voters said** (am; optional pm); **Lessons learned** (only once the digest's Scorecard says "ready"; afterwards once a week); **Coverage note**. End with https://jakeworcester-byte.github.io/political-tracker/#results
- **Still uncalled** has one line for every race in the digest's "Competitive races not yet called" list, Senate first: `- **SEN-XX / HOU-XX-NN** (final rating): Leader (P) leads by N pts, M% counted (source, time). One clause on what is outstanding, if sourced.` Then one line giving the number of safe seats still unmarked in the table, saying they are table lag rather than close races, with the site link. If nothing is uncalled, say so.
- Under 800 words outside the Still uncalled section. Every number and every call carries a source link. Never "I predict," "I expect," "will win," or "on track to win."
- On the first post-election morning, "Called since the last update" covers everything called overnight: flips first, then holds in competitive races, then one summary line for safe seats.

### P4. Publish

```bash
python3 scripts/publish.py --edition am        # or --edition pm
git add -A
git commit -m "Election update <today> am"     # or pm
git push origin HEAD:main
```

Push directly to `main`, as before. If rejected because `main` moved, `git pull --rebase origin main` and push again.

### P5. Notion (create-only, as in step 5)

- **Daily Briefs**: query for a page titled `Election update <today> morning` (or `evening`). If none, create it with the full update as the body. Properties: Date, Bottom line, Top movers (the competitive calls since the last update, one per line, or "none"), Senate control (markets) = "Called: D x, R y of 51; n uncalled", House control (markets) = "Called: D x, R y of 218; n uncalled", Generic ballot and Presidential approval = "n/a (post-election)", Coverage, Signals written, Site link (the #results link), Run status.
- **Signals**: as in step 5, one row per cited source not already present. Use the existing types (`reporting`, `poll` for exit polls and VoteCast); Race key = the race key or `National`.
- Skip the Races database. Inbox processing as in step 5.

### P6. Finish

Output the full text of `data/briefs/<slug>.md` followed by `Coverage: results tables <ok/failed>, <n> race news queries, <errors> errors. Site updated. Notion updated.` (or the honest equivalent). The phone notifier delivers the published file about 40 minutes after the run starts. If a core step failed (no update written or the push failed twice), the final message is a short failure notice, as in step 6.
