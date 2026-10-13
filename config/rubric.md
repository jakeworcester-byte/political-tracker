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

## Early vote

- Early ballots by party registration are participation signals, never vote counts. Never write that a party is "winning" the early vote.
- Mail ballots have skewed Democratic since 2020, so the change versus the same point in a prior cycle is the headline; the raw gap versus registration is context.
- Every early vote number carries its state, source link, and as-of date. Sources are state election offices, the UF Election Lab, or major outlets reporting official numbers; never social media accounts.

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
