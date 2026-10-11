# Routine prompt: Midterm Tracker morning run

This is the authoritative instruction set the cloud Routine follows every morning. Keep it in sync with the Routines at https://claude.ai/code/routines. Version: 2026-10-11c.

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

Use the Notion connector. Database IDs are in `CLAUDE.md`. If no Notion tools (`mcp__Notion__*`) are available in this session, skip this step entirely and say "Notion not updated (connector not attached to the routine)" in the final coverage line. Jake's Claude chats fall back to the raw GitHub data, so a skipped Notion update is a degradation, not a failure.

1. **Daily Briefs**: query for a page whose Date is today. If none, create one titled `Midterm brief <today>` with the full brief as the page body and these properties: Date, Bottom line (the first section's text), Top movers (one line per mover, or "none"), Senate control (markets), House control (markets), Generic ballot, Presidential approval, Coverage (`<ok>/<attempted> sources; failed: ...`), Signals written, Site link, Run status (OK if the core steps succeeded, Degraded if sources failed, Failed otherwise). If a page already exists for today, update its properties and replace its body.
2. **Signals**: create one row per source you cited in the brief (typically 10 to 20), skipping any URL that already has a Signals row dated today. Fields: Headline, Date, Race key or `National`, Type, Source, Source lean, Quality (per rubric), URL, Summary (your own words, one or two sentences), Why it matters (one line).
3. **Races**: using `notion_race_rows` from the bundle JSON, update rows whose Key matches and whose Consensus rating, Polling average, or Trend changed since the stored value; create rows for tracked races that do not exist yet. Write a one-line `State of the race` for races that appear in What moved or Races to watch.
4. **Inbox**: query rows where Processed is unchecked. For each, create a Signal of type `jake inbox`, lean `jake`, Summary = Jake's takeaway (never the article text), then check Processed. Mention these in the brief's spectrum section only if they are relevant today.

Keep Notion calls bounded: batch page creations where the tool allows it, and never write more than 60 pages in one run.

## 6. Finish

Output the full text of `data/briefs/<today>.md` followed by one line: `Coverage: <ok>/<attempted> sources. Site updated. Notion updated.` (or the honest equivalent if a step failed). The notifier routine delivers the published brief to Jake's phone about 40 minutes later, so the push to `main` is the deliverable.

If the run failed at a core step (no ratings, no brief, or the push failed twice), your final message is instead a short failure notice: what failed, the error text, and what Jake can do (open the run, or press Run now after the fix). Never end silently.
