# Rubric and guardrails

This file is versioned so every daily run applies the same rules. The routine reads it before writing a brief.

## What the system is

A synthesis of what credible forecasters, public polls, prediction markets, and reporting say about the 2026 midterms, with movement over time. It is not Jake's forecast and not Claude's. Never write "I expect" or "we predict." Write "forecasters rate," "polls show," "markets price."

## Consensus rating

- Scale, from the Democratic side: Solid D, Likely D, Lean D, Tilt D, Tossup, Tilt R, Lean R, Likely R, Solid R. "Safe" is a synonym for "Solid."
- Consensus is the median of the forecaster ratings available for that race on the Wikipedia ratings table (Cook, Sabato, Inside Elections, Decision Desk HQ, The Economist, Split Ticket, Fox, RealClearPolling, Silver Bulletin, FiftyPlusOne where present). Ties round toward Tossup.
- A race is **tracked** when its consensus is Lean D through Lean R inclusive, or when any two forecasters put it in that band. Likely and Solid races get a row in the table but no daily attention.
- Flag **forecaster disagreement** when ratings span four or more steps (for example Tossup to Solid D, or Lean D to Lean R). Tossup versus Likely is ordinary spread, not disagreement.

## Source quality scoring (1 to 5, explainable)

Polls
- 5: nonpartisan, transparent methodology, likely voters, n >= 600, fielded within 10 days.
- 4: as above but registered voters or n 400 to 599.
- 3: partisan-sponsored or internal poll released publicly; label the sponsor and lean.
- 2: online opt-in or unclear methodology.
- 1: no methodology, no sample size, or more than 21 days old.

Reporting
- 5: original reporting with named sources from a major outlet or the leading local outlet in that state or district.
- 4: original reporting with anonymous sourcing, or strong local reporting.
- 3: aggregation of others' reporting; wire summaries.
- 2: single-source or thinly sourced.
- 1: rumor, social media, unverified.

Punditry and podcasts
- Always labeled as opinion with a lean tag. Score 3 by default. Never presented as fact. Useful for "what the spectrum is saying," never for "what happened."

Markets
- A separate signal type, always labeled "market" and shown as an implied probability with the platform name. Never averaged with polls.

## What counts as news worth surfacing

- A forecaster rating change on any tracked race.
- A polling-average shift of 2.0 points or more in a tracked race since the last run, or a new high-quality poll (score 4 or 5) that differs from the average by 4 or more points.
- A market move of 5 points or more in a chamber-control or tracked-race market since the last run.
- A material race event: candidate withdrawal, major scandal, debate, large ad buy, high-profile endorsement, court ruling affecting the ballot, or a redistricting change.
- Generic ballot or presidential approval moving 1.5 points or more in the average since the last run.
- When the digest marks generic ballot polls STALE, never describe the average as "unchanged" or current on the strength of those polls. Cite the aggregator average when the digest has one; otherwise say the poll data is stale and give the date of the newest poll.

## Early vote

- Early ballots by party registration are participation signals, never vote counts. Never write that a party is "winning" the early vote.
- Mail ballots have skewed Democratic since 2020, so the change versus the same point in a prior cycle is the headline; the raw gap versus registration is context.
- Every early vote number carries its state, source link, and as-of date. Sources are state election offices, the UF Election Lab, or major outlets reporting official numbers; never social media accounts.
- Lead with **who is voting**, not just the party split: voter-file analysis of new voters, people who skipped the last midterm, habitual versus low-propensity voters, age and race, compared with the same point in prior cycles. Party-registration shares alone are the weakest read; use them as context.
- Preferred voter-file analysts, attributed by name: Jon Ralston (The Nevada Independent / Ralston Reports) for Nevada, Michael Bitzer (Catawba College, Center for NC Politics) for North Carolina, and major-outlet analyses of L2 or state voter files (ABC, NYT, WaPo, CNN, AP). Analyses from party-aligned data firms (TargetSmart and Catalist on the left, or Republican-aligned firms) may be cited only with that label. Read the analyst's own post, not a social-media summary of it.
- An analyst's modeled estimate or prediction (for example Bitzer's registration-based model or Ralston's final predictions) is labeled as theirs and as a model or prediction, never as a count or as our view.

## Hard guardrails

1. Every factual claim in a brief traces to a stored signal with a URL. No unsourced claims. If you cannot cite it, cut it.
2. Numbers carry their date and their source: "Collins 45, Jackson 49, Hart Research for Senate Majority PAC, July 27 to August 1 (partisan sponsor)."
3. Polls and opinions are labeled as such. Markets are labeled as markets.
4. Balance: when commentary is included, the brief reflects at least one left-leaning and one right-leaning perspective. The coverage note names an imbalance when sources on one side failed.
5. Paraphrase only. No copied article text beyond a short attributed quote when exact wording truly matters. Never store full text from paywalled sources. Jake's Inbox entries hold a headline, URL, and his own takeaway only.
6. If a source failed, say so in the coverage note. Never silently work around it or substitute a scraped mirror.
7. If nothing meaningful changed, the brief says so in one line. Do not manufacture drama.
8. Never store or publish Jake's Inbox takeaways on the public site unless the entry is marked Public.
9. Respect rate limits and robots. One request per page per run with an identifying user agent. Never bypass a bot challenge, paywall, or terms of service.

## Brief shape (two minutes on a phone)

1. Bottom line: two or three sentences on the state of both chambers.
2. What moved: rating changes, poll shifts, market swings since yesterday, each with a number and a source.
3. Races to watch today: three to five, one line each.
4. Early vote (through Election Day): two to four lines on early ballots by party versus registration and versus the same point in a prior cycle, sourced and dated. Party registration is not party vote; say so.
5. What the spectrum is saying: a short left / center / right read of commentary, labeled by lean.
6. Talking points: two or three things worth being able to say in conversation today.
7. Coverage note: one line on sources that failed, or "all sources reported."
8. Link to the site.

## Post-election (from 2026-11-04; config/postelection.json)

From the date in `config/postelection.json` the morning brief becomes an **election update**, with an evening edition through `evening_editions_through`. The guardrails above still apply, plus:

1. **We never call a race.** A race is "called" only when AP or a network has projected it, and the brief names who: "AP called it for X." The Wikipedia results table is the trigger for checking, not the attribution; cite a news link confirming the call when one exists, otherwise write "marked called in the Wikipedia results table, following AP" and link the table.
1a. **Reported calls fill the table's lag.** Races the digest lists under "Reported called in news" (a projection cited, or two outlets agreeing, after polls closed) may be written as "<Outlet> reports X won" and counted in a separate tally labeled "including reported calls." Never merge them silently with table calls, never upgrade a victory claim or a lead into a call, and drop a reported call if later headlines conflict.
2. **Projected is not certified.** Use "called" or "projected" until the state certifies; "certified" only with the state's or county's own announcement or a major outlet reporting it.
3. **Leads are counts, not outcomes.** For an uncalled race write the leader, the margin, and the share counted when a source gives it, with the source and time ("X leads by 1.2 points with about 80% counted, per AP via NYT, 11:40 p.m."). Never "X will win," "X is on track," or "likely to win."
4. **Recount language needs state law.** Say a race "could go to a recount" or "is within the automatic recount margin" only with a source stating that state's threshold. Margins under one point are flagged in the digest; check before writing.
5. **Claims of fraud, irregularity, or intervention are claims.** Attribute them to the person or agency making them, and pair them with what officials, courts, or election administrators have said in response when a source reports it. Court actions name the court, the parties, and the stage (filed, hearing set, ruled, appealed).
6. **Exit polls are surveys.** Label AP VoteCast and network exit polls as surveys of voters, say which one, and never treat early exit-poll numbers as results. Exit-poll numbers are revised as counts come in; say "early" when they are.
7. **Every uncalled race gets a line.** Every competitive race (final consensus Likely D through Likely R) that is not called, and every safe-rated seat whose count is within 5 points, gets one line in "Still uncalled." Safe seats that are only waiting on the table get a count, with a link to the full list on the site.
9. **Flag safe seats that turned competitive.** A seat rated Solid (or unrated) before the election whose count is now within 5 points is marked "(rated safe)" wherever it appears, called or not. When any exist, the Bottom line says how many, because an unexpected close race is news in itself. The digest lists them under "Rated safe, now competitive"; the site highlights them in violet.
8. **No partisan legal trackers as sources.** Party, campaign, and advocacy litigation trackers (for example Democracy Docket) can point to a case, but the cited source is the court record or a news report.

### Election update shape (phone-first; Still uncalled is exempt from the word budget)

1. Bottom line: where each chamber stands in calls (seats won versus 218 and 51), whether either has been called and by whom.
2. Called since the last update: competitive calls, one line each with the call's source; flips first. Safe seats called as rated get one summary line.
3. Still uncalled: one line per race as above, Senate first, then by closeness.
4. Recounts and certification: recount triggers, canvass and certification steps, ranked-choice tabulation dates (Maine, Alaska), each sourced; "none reported" is fine.
5. Legal and federal action: lawsuits, rulings, and any federal or administration action affecting counting or certification, attributed per rule 5; "none reported" is fine.
6. What voters said: two to four lines from exit polls or VoteCast, labeled as surveys, plus who actually turned out from voter-file analysis (Jon Ralston for Nevada, Michael Bitzer for North Carolina, major-outlet L2 or state-file analyses), compared with 2022 and attributed by name (morning editions; optional in the evening).
7. Lessons learned: once the digest's Scorecard says "ready," a short read on how forecasters, polls, and markets did; then weekly. Before that, omit.
8. Coverage note: sources that failed, table lag, and whether CourtListener court records were used (or why not: no token, token refused).
9. Link to the site's Results tab.
