# Routine prompt: Midterm Tracker morning run

This is the authoritative instruction set the cloud Routine follows every morning. Keep it in sync with the Routines at https://claude.ai/code/routines. Version: 2026-10-11d.

## How the routines are wired

Two routines, because a fresh routine session has no repository credential and no connectors, while a persistent session keeps both:

1. **Midterm Tracker morning run** (5:07 a.m. Central) fires into the persistent runner session `session_01DfgwjsFRTn4HqAT89fuSzh`, which was created with the repository as its source (push access to `main`) and carries the Notion connector. It does everything below.
2. **Midterm Tracker brief to phone** (5:47 a.m. Central) is a fresh-session routine with push and email notifications on. It only fetches `docs/data/briefs/<today>.md` from raw GitHub and outputs it verbatim, so its completion notification on Jake's phone *is* the brief. If no brief is published by then, it notifies Jake of the failure instead.

If the runner session is ever archived or expires, create a new session with `source_url` = the repository and `outcome_branch` = `main`, then rebind the morning routine to its id (`persistent_session_id`).

---

You are running Jake Worcester's Midterm Tracker morning update inside the repository `jakeworcester-byte/political-tracker`. Work autonomously; nobody is watching. Read `CLAUDE.md` and `config/rubric.md` first and follow them exactly. The whole run should take well under 30 minutes.

Today's date is the current date in America/Chicago.

## 1. Collect and analyze

If the repository is not already in your working directory (a fresh routine session may start empty), clone it first: `git clone https://github.com/jakeworcester-byte/political-tracker && cd political-tracker`. If a later push is refused for lack of credentials, call the `add_repo` tool with owner `jakeworcester-byte`, repo `political-tracker`, access `push`, then retry the push.

```bash
git checkout main && git pull --ff-only origin main
python3 scripts/collect.py
python3 scripts/analyze.py
```

If `collect.py` exits with code 2, both ratings tables failed. Retry once after 60 seconds. If it fails again, skip to step 6 and write a failure notice instead of a brief.

## 2. Read the digest

Read `data/bundle/<today>.md` in full. It contains: national numbers, chamber overview, exactly what moved since yesterday (numeric), every tracked race with its ratings, polls, and trend, recent signals grouped by source lean, and the coverage report. Also read `data/bundle/<today>.json` for the Notion race rows.

If the digest lists races under "unresolved candidate parties," look at the Google News headlines for those races in `data/raw/<today>/google_news.json`. When a headline makes a candidate's party unambiguous (for example "Democrat Jane Doe" or "(D)"), add the mapping to `config/candidates.json` with the source in `_sources`, then rerun `python3 scripts/analyze.py`. Do not guess.

## 3. Write the brief

Write `data/briefs/<today>.md` following the brief shape in `config/rubric.md`. Hard rules:

- Title line: `# Midterm brief: <Weekday, Month D>`.
- Sections, in order, as `## ` headings: Bottom line; What moved; Races to watch today; What the spectrum is saying; Talking points; Coverage note. End with a line linking the site: https://jakeworcester-byte.github.io/political-tracker/
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

Push directly to `main`; do not create a `claude/` branch or a pull request. GitHub Pages redeploys from `main:/docs` automatically. If the push is rejected because `main` moved, run `git pull --rebase origin main` and push again.

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
