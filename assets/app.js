/* Jake's Political Tracker: static front end over docs/data/*.json. No build step, no framework. */
(function () {
  "use strict";
  const $app = document.getElementById("app");
  const $tabs = document.getElementById("tabs");
  const $footMeta = document.getElementById("foot-meta");
  let DATA = null, HISTORY = null;
  const raceCache = {};

  const SIDE = r => r === "Tossup" ? "T" : r.endsWith(" D") ? "D" : "R";
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = iso => { if (!iso) return ""; const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso); return isNaN(d) ? iso : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: iso.length === 10 ? undefined : undefined }); };
  const pct = v => v == null ? "n/a" : `${Math.round(v)}%`;
  const margin = m => m == null ? "n/a" : Math.abs(m) < 0.05 ? "Even" : `${m < 0 ? "D" : "R"}+${Math.abs(m).toFixed(1)}`;
  const trendHtml = t => t === "Toward D" ? '<span class="trend D">▲ toward D</span>' : t === "Toward R" ? '<span class="trend R">▲ toward R</span>' : '<span class="trend meta">stable</span>';
  const pill = (rating, cls = "") => `<span class="pill ${SIDE(rating)} ${cls}">${esc(rating)}</span>`;

  // Minimal Markdown: headings, bold, italics, links, bullets, paragraphs.
  function md(src) {
    const lines = (src || "").split(/\r?\n/);
    let out = "", inList = false;
    const inline = s => {
      const links = [];
      const hold = html => { links.push(html); return `\u0000${links.length - 1}\u0000`; };
      let t = esc(s)
        .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (m, text, url) => hold(`<a href="${url}" target="_blank" rel="noopener">${text}</a>`))
        .replace(/(^|[^\w"=])(https?:\/\/[^\s<)]+)/g, (m, pre, url) => pre + hold(`<a href="${url}" target="_blank" rel="noopener">${url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 40)}${url.length > 48 ? "…" : ""}</a>`))
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|\s)\*([^*]+)\*/g, "$1<em>$2</em>");
      return t.replace(/\u0000(\d+)\u0000/g, (m, i) => links[+i]);
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (/^\s*[-*]\s+/.test(line)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`; continue; }
      if (inList) { out += "</ul>"; inList = false; }
      if (!line.trim()) continue;
      const h = /^(#{1,3})\s+(.*)$/.exec(line);
      if (h) { out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
      if (/^\d+\.\s+/.test(line)) { out += `<p>${inline(line)}</p>`; continue; }
      out += `<p>${inline(line)}</p>`;
    }
    if (inList) out += "</ul>";
    return out;
  }

  async function load() {
    try {
      const r = await fetch("data/latest.json", { cache: "no-store" });
      if (!r.ok) throw new Error(`latest.json ${r.status}`);
      DATA = await r.json();
      fetch("data/history.json", { cache: "no-store" }).then(x => x.ok ? x.json() : []).then(h => { HISTORY = h; if (location.hash.startsWith("#national") || !location.hash || location.hash === "#overview") route(); }).catch(() => {});
      $footMeta.textContent = `Data as of ${new Date(DATA.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}. Ratings compiled from the forecasters' published tables via Wikipedia; polls from VoteHub; markets from Polymarket. Built with Claude Code.`;
      route();
    } catch (e) {
      $app.innerHTML = `<div class="error">Could not load data (${esc(e.message)}). Try again in a minute.</div>`;
    }
  }

  function setTab(name) {
    [...$tabs.querySelectorAll("a")].forEach(a => a.classList.toggle("active", a.dataset.tab === name));
  }

  function route() {
    if (!DATA) return;
    const hash = location.hash.replace(/^#/, "") || "overview";
    const [view, arg] = hash.split("/");
    window.scrollTo({ top: 0 });
    switch (view) {
      case "brief": setTab("brief"); return renderBrief();
      case "briefs": setTab("brief"); return renderBriefArchive(arg);
      case "senate": setTab("senate"); return renderChamber("Senate");
      case "house": setTab("house"); return renderChamber("House");
      case "race": setTab(arg && arg.startsWith("SEN") ? "senate" : "house"); return renderRace(arg);
      case "national": setTab("national"); return renderNational();
      case "about": setTab("about"); return renderAbout();
      default: setTab("overview"); return renderOverview();
    }
  }

  // ------------------------------------------------------------------ overview
  function controlCard(label, m, extra) {
    if (!m || m.D == null) return `<div class="card"><div class="label">${label}</div><div class="big">n/a</div><div class="note">market unavailable</div></div>`;
    const lead = m.D >= 50 ? `D ${pct(m.D)}` : `R ${pct(m.R)}`;
    return `<div class="card"><div class="label">${label}</div><div class="big" style="color:${m.D >= 50 ? "var(--dem)" : "var(--rep)"}">${lead}</div>
      <div class="note">${extra || ""}<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.source)}</a> implied probability</div></div>`;
  }

  function renderOverview() {
    const n = DATA.national, ov = DATA.overview, s = ov.senate, b = DATA.brief;
    const senate = DATA.races.filter(r => r.chamber === "Senate");
    const house = DATA.races.filter(r => r.chamber === "House");
    const sb = bucketCounts(senate), hb = bucketCounts(house);
    const movers = DATA.movers || [];
    const watch = DATA.races.filter(r => r.tracked).sort((a, b) => Math.abs(a.consensus_score) - Math.abs(b.consensus_score) || (b.signal_count - a.signal_count)).slice(0, 6);
    $app.innerHTML = `
      <h1>State of play</h1>
      <p class="sub">Updated ${fmtDate(DATA.run_date)} · what forecasters, polls, and markets say today. <a href="#about">Not a forecast.</a></p>
      <div class="cards">
        ${controlCard("Senate control", n.markets.senate)}
        ${controlCard("House control", n.markets.house)}
        <div class="card"><div class="label">Generic ballot</div><div class="big" style="color:${(n.generic_ballot.average_margin || 0) < 0 ? "var(--dem)" : "var(--rep)"}">${esc(n.generic_ballot.average_label)}</div><div class="note">avg of ${n.generic_ballot.n_polls} polls · <a href="#national">details</a></div></div>
        <div class="card"><div class="label">Presidential approval</div><div class="big">${n.approval.approve == null ? "n/a" : n.approval.approve.toFixed(0) + "%"}</div><div class="note">${n.approval.net == null ? "" : `net ${n.approval.net > 0 ? "+" : ""}${n.approval.net.toFixed(1)} · ${n.approval.n_polls} polls`}</div></div>
      </div>

      <h2>Senate: 100 seats, 51 for a majority</h2>
      <div class="card">
        ${seatBar(s.not_up.D + sb["Solid D"] + sb["Likely D"], sb["Lean D"] + sb["Tilt D"], sb["Tossup"], sb["Lean R"] + sb["Tilt R"], s.not_up.R + sb["Solid R"] + sb["Likely R"], 100)}
        <div class="legend"><span><i style="background:var(--dem)"></i>Solid/Likely D ${s.not_up.D + sb["Solid D"] + sb["Likely D"]}</span><span><i style="background:color-mix(in srgb,var(--dem) 55%,var(--surface))"></i>Lean/Tilt D ${sb["Lean D"] + sb["Tilt D"]}</span><span><i style="background:var(--toss)"></i>Tossup ${sb["Tossup"]}</span><span><i style="background:color-mix(in srgb,var(--rep) 55%,var(--surface))"></i>Lean/Tilt R ${sb["Lean R"] + sb["Tilt R"]}</span><span><i style="background:var(--rep)"></i>Solid/Likely R ${s.not_up.R + sb["Solid R"] + sb["Likely R"]}</span></div>
        <p class="meta" style="margin:8px 0 0">Consensus = median of ${DATA.sources.forecasters.length} forecasters per race. Seats not on the ballot counted with their current party (${s.not_up.D} D, ${s.not_up.R} R). <a href="#senate">All 35 races →</a></p>
      </div>

      <h2>House: 435 seats, 218 for a majority</h2>
      <div class="card">
        ${houseBar(ov.house_forecasters)}
        <p class="meta" style="margin:8px 0 0"><a href="#house">${ov.house.tracked} competitive seats tracked →</a></p>
      </div>

      ${b ? `<h2>Today's brief</h2><div class="brief">${md(b.markdown.split(/\n## /)[0])}<p><a class="btn" href="#brief">Read the full brief →</a></p></div>` : `<h2>Today's brief</h2><div class="callout">The morning brief has not been published yet today.</div>`}

      <h2>What moved</h2>
      ${movers.length ? `<ul class="list">${movers.map(m => `<li><div class="item-title">${esc(m.name)} <span class="pill gray sm">${esc(m.type)}</span></div><div class="item-meta">${esc(m.text)} · ${m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.source)}</a>` : esc(m.source)}</div></li>`).join("")}</ul>` : `<div class="callout">No rating, polling-average, or market change crossed a reporting threshold since the last run.</div>`}

      <h2>Races to watch</h2>
      <div class="race-grid">${watch.map(raceCard).join("")}</div>
    `;
  }

  function bucketCounts(rows) {
    const b = { "Solid D": 0, "Likely D": 0, "Lean D": 0, "Tilt D": 0, "Tossup": 0, "Tilt R": 0, "Lean R": 0, "Likely R": 0, "Solid R": 0 };
    rows.forEach(r => b[r.consensus]++);
    return b;
  }
  function seatBar(d, dl, t, rl, r, total) {
    const w = v => `${(100 * v / total).toFixed(2)}%`;
    return `<div class="bar" role="img" aria-label="seat distribution"><span class="d" style="width:${w(d)}"></span><span class="dl" style="width:${w(dl)}"></span><span class="t" style="width:${w(t)}"></span><span class="rl" style="width:${w(rl)}"></span><span class="r" style="width:${w(r)}"></span></div>`;
  }
  function houseBar(fc) {
    const keys = Object.keys(fc || {});
    if (!keys.length) return '<div class="meta">Forecaster seat totals unavailable.</div>';
    const avg = k => Math.round(keys.reduce((a, x) => a + (fc[x][k] || 0), 0) / keys.length);
    return `${seatBar(avg("D"), 0, avg("tossups"), 0, avg("R"), 435)}<div class="legend"><span><i style="background:var(--dem)"></i>D ${avg("D")}</span><span><i style="background:var(--toss)"></i>Tossup ${avg("tossups")}</span><span><i style="background:var(--rep)"></i>R ${avg("R")}</span><span class="meta">average across ${keys.length} forecasters</span></div>
    <div class="table-scroll" style="box-shadow:none;margin-top:10px"><table class="ratings"><thead><tr><th>Forecaster</th><th>D</th><th>R</th><th>Tossup</th><th>Lean</th></tr></thead><tbody>${keys.map(k => { const v = fc[k]; const t = v.tossups || 0; const lean = v.D - v.R; return `<tr><td>${esc(v.name)}</td><td style="color:var(--dem);font-weight:700">${v.D}</td><td style="color:var(--rep);font-weight:700">${v.R}</td><td>${t}</td><td><span class="pill ${lean > 0 ? "D" : lean < 0 ? "R" : "T"} sm">${lean > 0 ? "D" : lean < 0 ? "R" : "Even"}${lean ? " +" + Math.abs(lean) : ""}</span></td></tr>`; }).join("")}</tbody></table></div>`;
  }

  function raceCard(r) {
    const side = SIDE(r.consensus);
    const poll = r.poll ? `polls <b>${esc(r.poll.average_label)}</b> (${r.poll.n_polls})` : `<span class="meta">no public polls</span>`;
    return `<a class="race ${side}" href="#race/${r.key}">
      <div class="name"><span>${esc(r.name)}</span>${pill(r.consensus, "sm")}</div>
      <div class="inc">${r.open_seat ? "Open seat" : esc(r.incumbent)}${r.incumbent_party ? ` (${r.incumbent_party})` : ""}${r.pvi ? ` · ${esc(r.pvi)}` : ""}</div>
      <div class="row"><span>${poll}</span>${trendHtml(r.trend)}${r.disagreement ? '<span class="pill gray sm">forecasters split</span>' : ""}</div>
    </a>`;
  }

  // ------------------------------------------------------------------ chamber
  let chamberFilter = "tracked";
  function renderChamber(ch) {
    const all = DATA.races.filter(r => r.chamber === ch);
    const filters = [["tracked", `Competitive (${all.filter(r => r.tracked).length})`], ["all", `All rated (${all.length})`], ["D", "D-held"], ["R", "R-held"]];
    const rows = all.filter(r => chamberFilter === "all" ? true : chamberFilter === "tracked" ? r.tracked : r.incumbent_party === chamberFilter)
      .sort((a, b) => a.consensus_score - b.consensus_score || a.name.localeCompare(b.name));
    const dates = DATA.forecaster_dates[ch.toLowerCase()] || {};
    const b = bucketCounts(all);
    $app.innerHTML = `
      <h1>${ch} ${ch === "Senate" ? "races" : "battlefield"}</h1>
      <p class="sub">${ch === "Senate" ? "All 35 seats on the ballot, including the Florida and Ohio specials." : "Every district the forecasters rate as anything short of safe."} Consensus is the median of the forecasters' current ratings. Tap a race for detail.</p>
      <div class="card">${seatBar(b["Solid D"] + b["Likely D"], b["Lean D"] + b["Tilt D"], b["Tossup"], b["Lean R"] + b["Tilt R"], b["Solid R"] + b["Likely R"], all.length)}
        <div class="legend"><span><i style="background:var(--dem)"></i>Solid/Likely D ${b["Solid D"] + b["Likely D"]}</span><span><i style="background:color-mix(in srgb,var(--dem) 55%,var(--surface))"></i>Lean/Tilt D ${b["Lean D"] + b["Tilt D"]}</span><span><i style="background:var(--toss)"></i>Tossup ${b["Tossup"]}</span><span><i style="background:color-mix(in srgb,var(--rep) 55%,var(--surface))"></i>Lean/Tilt R ${b["Lean R"] + b["Tilt R"]}</span><span><i style="background:var(--rep)"></i>Solid/Likely R ${b["Solid R"] + b["Likely R"]}</span></div></div>
      <div class="filters">${filters.map(([k, l]) => `<button data-f="${k}" class="${chamberFilter === k ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="race-grid">${rows.map(raceCard).join("")}</div>
      <h2>Rating by forecaster</h2>
      <div class="table-scroll"><table class="ratings"><thead><tr><th>Race</th><th>Consensus</th>${Object.keys(dates).map(k => `<th>${esc(shortName(k))}<br><span class="meta">${fmtDate(dates[k])}</span></th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr><td><a href="#race/${r.key}">${esc(r.name)}</a></td><td>${pill(r.consensus, "sm")}</td>${Object.keys(dates).map(k => `<td>${r.ratings[k] ? pill(r.ratings[k].rating, "sm") : '<span class="meta">—</span>'}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="meta">Sources: forecasters' published ratings as compiled on <a href="${esc(DATA.sources.ratings[ch === "Senate" ? 0 : 1])}" target="_blank" rel="noopener">Wikipedia</a>, with each forecaster's update date in the header.</p>`;
    $app.querySelectorAll(".filters button").forEach(btn => btn.addEventListener("click", () => { chamberFilter = btn.dataset.f; renderChamber(ch); }));
  }
  const SHORT = { cook: "Cook", ddhq: "DDHQ", econ: "Economist", fpo: "FiftyPlusOne", fox: "Fox", ie: "Inside Elections", rcp: "RCP", sabato: "Sabato", silver: "Silver Bulletin", st: "Split Ticket" };
  const shortName = k => SHORT[k] || k;

  // ------------------------------------------------------------------ race detail
  async function renderRace(key) {
    const r = DATA.races.find(x => x.key === key);
    if (!r) { $app.innerHTML = `<div class="error">Unknown race ${esc(key)}.</div>`; return; }
    $app.innerHTML = `<a class="back" href="#${r.chamber === "Senate" ? "senate" : "house"}">← ${r.chamber}</a><h1>${esc(r.name)} ${pill(r.consensus)}</h1><div class="loading">Loading detail…</div>`;
    let d = raceCache[key];
    if (!d) {
      try { const resp = await fetch(`data/races/${key}.json`, { cache: "no-store" }); d = resp.ok ? await resp.json() : null; raceCache[key] = d; } catch (e) { d = null; }
    }
    if (location.hash !== `#race/${key}`) return;
    const ratings = Object.entries(r.ratings).map(([k, v]) => `<li><div class="item-title">${esc(v.name)} ${pill(v.rating, "sm")}${v.flip ? ' <span class="pill gray sm">flip</span>' : ""}</div><div class="item-meta">as of ${fmtDate(v.as_of)}</div></li>`).join("");
    const poll = r.poll;
    const polls = d && d.poll && d.poll.polls ? d.poll.polls : [];
    const signals = d && d.signals ? d.signals : [];
    const hist = d && d.history ? d.history.filter(h => h.p != null) : [];
    $app.innerHTML = `
      <a class="back" href="#${r.chamber === "Senate" ? "senate" : "house"}">← ${r.chamber}</a>
      <h1>${esc(r.name)} ${pill(r.consensus)}</h1>
      <p class="sub">${r.open_seat ? `Open seat (${esc(r.incumbent)} ${esc(d && d.incumbent_note || "not running")})` : `Incumbent ${esc(r.incumbent)} (${r.incumbent_party})`} · ${r.pvi ? `PVI ${esc(r.pvi)} · ` : ""}last result ${esc(r.last_result)} · ${trendHtml(r.trend)}</p>
      <div class="cards">
        <div class="card"><div class="label">Polling average</div><div class="big" style="color:${poll && poll.average_margin < 0 ? "var(--dem)" : poll ? "var(--rep)" : "inherit"}">${poll ? esc(poll.average_label) : "n/a"}</div><div class="note">${poll ? `${poll.n_polls} public polls, last ${poll.window_days} days` : "no polls with resolvable candidates"}</div></div>
        <div class="card"><div class="label">Latest poll</div><div class="big">${poll ? esc(poll.latest.margin_label) : "n/a"}</div><div class="note">${poll ? `${esc(poll.latest.pollster)}, ${fmtDate(poll.latest.end_date)}${poll.latest.partisan ? ` · ${esc(poll.latest.partisan)} sponsor` : ""}` : ""}</div></div>
        <div class="card"><div class="label">Forecasters</div><div class="big">${Object.keys(r.ratings).length}</div><div class="note">${r.disagreement ? "split by four or more steps" : "broadly aligned"} · ${r.flip_votes} call a flip</div></div>
      </div>
      ${hist.length > 1 ? `<h2>Polling average over time</h2><div class="card">${sparkline(hist.map(h => ({ x: h.date, y: h.p })), true)}<div class="meta">R minus D margin of the rolling polling average, by run date.</div></div>` : ""}
      <div class="two">
        <div><h2>Ratings</h2><div class="card"><ul class="list">${ratings}</ul></div></div>
        <div><h2>Recent polls</h2><div class="card">${polls.length ? `<ul class="list">${polls.map(p => `<li><div class="item-title">${esc(p.pollster)} <span class="pill ${p.margin < 0 ? "D" : p.margin > 0 ? "R" : "T"} sm">${margin(p.margin)}</span></div><div class="item-meta">${fmtDate(p.end_date)} · n=${p.n || "?"} ${esc(p.population || "")}${p.partisan ? ` · ${esc(p.partisan)} sponsor` : ""} · ${(p.answers || []).map(a => `${esc(a.choice)} ${a.pct}`).join(", ")}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""}</div></li>`).join("")}</ul>` : '<div class="meta">No public polls found for this race in the VoteHub feed.</div>'}</div></div>
      </div>
      <h2>Recent reporting and commentary</h2>
      <div class="card">${signals.length ? `<ul class="list">${signals.slice(0, 15).map(s => `<li><div class="item-title"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></div><div class="item-meta">${esc(s.source)}${s.lean && s.lean !== "unrated" ? ` · <span class="pill gray sm">${esc(s.lean)}</span>` : ""} · ${fmtDate(s.date)} · ${esc(s.type)}</div></li>`).join("")}</ul>` : '<div class="meta">Nothing matched this race in the last few days of feeds.</div>'}</div>
      <p class="meta">Headlines are linked, not reproduced. Lean tags describe the outlet, not the story.</p>`;
  }

  function sparkline(points, isMargin) {
    const w = 600, h = 70, pad = 6;
    const ys = points.map(p => p.y);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (isMargin) { lo = Math.min(lo, -1); hi = Math.max(hi, 1); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const X = i => pad + (w - 2 * pad) * (points.length === 1 ? 0.5 : i / (points.length - 1));
    const Y = v => pad + (h - 2 * pad) * (1 - (v - lo) / (hi - lo));
    const path = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
    const zero = isMargin && lo < 0 && hi > 0 ? `<line x1="${pad}" x2="${w - pad}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="var(--line)" stroke-dasharray="4 4"/>` : "";
    const last = points[points.length - 1];
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="trend">${zero}<path d="${path}" fill="none" stroke="${isMargin ? (last.y < 0 ? "var(--dem)" : "var(--rep)") : "var(--accent)"}" stroke-width="2.5" stroke-linejoin="round"/><circle cx="${X(points.length - 1).toFixed(1)}" cy="${Y(last.y).toFixed(1)}" r="4" fill="${isMargin ? (last.y < 0 ? "var(--dem)" : "var(--rep)") : "var(--accent)"}"/></svg><div class="meta">${fmtDate(points[0].x)} → ${fmtDate(last.x)} · now ${isMargin ? margin(last.y) : last.y}</div>`;
  }

  // ------------------------------------------------------------------ national
  function renderNational() {
    const n = DATA.national, gb = n.generic_ballot, ap = n.approval, m = n.markets;
    const hist = (HISTORY || []).filter(h => h.generic_ballot != null);
    const mh = (HISTORY || []).filter(h => h.markets && h.markets.senate != null);
    $app.innerHTML = `
      <h1>Where the electorate is</h1>
      <p class="sub">Generic ballot, presidential approval, and prediction markets. Polls from the VoteHub public feed; markets from Polymarket.</p>
      <div class="cards">
        <div class="card"><div class="label">Generic ballot</div><div class="big" style="color:${(gb.average_margin || 0) < 0 ? "var(--dem)" : "var(--rep)"}">${esc(gb.average_label)}</div><div class="note">avg of ${gb.n_polls} polls</div></div>
        <div class="card"><div class="label">Approve</div><div class="big">${ap.approve == null ? "n/a" : ap.approve.toFixed(1) + "%"}</div><div class="note">${ap.n_polls} polls</div></div>
        <div class="card"><div class="label">Disapprove</div><div class="big">${ap.disapprove == null ? "n/a" : ap.disapprove.toFixed(1) + "%"}</div><div class="note">net ${ap.net == null ? "" : (ap.net > 0 ? "+" : "") + ap.net.toFixed(1)}</div></div>
        ${controlCard("Senate control", m.senate)}
        ${controlCard("House control", m.house)}
      </div>
      ${hist.length > 1 ? `<h2>Generic ballot average over time</h2><div class="card">${sparkline(hist.map(h => ({ x: h.date, y: h.generic_ballot })), true)}</div>` : ""}
      ${mh.length > 1 ? `<h2>Market odds of Democratic Senate control</h2><div class="card">${sparkline(mh.map(h => ({ x: h.date, y: h.markets.senate })), false)}</div>` : ""}
      ${m.balance ? `<h2>Balance of power (Polymarket)</h2><div class="card"><ul class="list">${Object.entries(m.balance.combos).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<li><div class="item-title">${esc(k)} <span class="pill gray sm">${v}%</span></div></li>`).join("")}</ul><p class="meta"><a href="${esc(m.balance.url)}" target="_blank" rel="noopener">Market page</a> · implied probabilities, labeled as market prices and never averaged with polls.</p></div>` : ""}
      <div class="two">
        <div><h2>Recent generic ballot polls</h2><div class="card"><ul class="list">${gb.polls.map(p => `<li><div class="item-title">${esc(p.pollster)} <span class="pill ${p.margin < 0 ? "D" : p.margin > 0 ? "R" : "T"} sm">${margin(p.margin)}</span></div><div class="item-meta">${fmtDate(p.end_date)} · n=${p.n || "?"} ${esc(p.population || "")}${(p.sponsors || []).length ? ` · ${esc(p.sponsors.join(", "))}` : ""}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""}</div></li>`).join("")}</ul></div></div>
        <div><h2>Recent approval polls</h2><div class="card"><ul class="list">${ap.polls.map(p => `<li><div class="item-title">${esc(p.pollster)} <span class="pill gray sm">${p.approve}/${p.disapprove}</span></div><div class="item-meta">${fmtDate(p.end_date)} · n=${p.n || "?"} ${esc(p.population || "")}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""}</div></li>`).join("")}</ul></div></div>
      </div>
      <h2>National reporting and commentary</h2>
      <div class="card"><ul class="list">${(DATA.national_signals || []).slice(0, 25).map(s => `<li><div class="item-title"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></div><div class="item-meta">${esc(s.source)}${s.lean && s.lean !== "unrated" ? ` · <span class="pill gray sm">${esc(s.lean)}</span>` : ""} · ${fmtDate(s.date)} · ${esc(s.type)}</div></li>`).join("")}</ul></div>`;
  }

  // ------------------------------------------------------------------ brief
  function renderBrief() {
    const b = DATA.brief;
    $app.innerHTML = b ? `<div class="brief">${md(b.markdown)}</div><p class="meta" style="margin-top:12px">Published ${new Date(b.published_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })} · <a href="#briefs">Past briefs</a></p>`
      : `<h1>Morning brief</h1><div class="callout">No brief has been published yet today. <a href="#briefs">Past briefs</a></div>`;
  }
  async function renderBriefArchive(date) {
    $app.innerHTML = `<h1>Past briefs</h1><div class="loading">Loading…</div>`;
    let idx = { dates: [] };
    try { const r = await fetch("data/briefs/index.json", { cache: "no-store" }); if (r.ok) idx = await r.json(); } catch (e) { /* no archive yet */ }
    if (date) {
      try { const r = await fetch(`data/briefs/${date}.md`, { cache: "no-store" }); const txt = r.ok ? await r.text() : "Brief not found."; $app.innerHTML = `<a class="back" href="#briefs">← All briefs</a><div class="brief">${md(txt)}</div>`; } catch (e) { $app.innerHTML = `<div class="error">Could not load that brief.</div>`; }
      return;
    }
    $app.innerHTML = `<h1>Past briefs</h1>${idx.dates.length ? `<div class="card"><ul class="list">${idx.dates.map(d => `<li><a href="#briefs/${d}" class="item-title">${fmtDate(d)}</a></li>`).join("")}</ul></div>` : '<div class="callout">No briefs archived yet.</div>'}`;
  }

  // ------------------------------------------------------------------ about
  function renderAbout() {
    const c = DATA.coverage || {};
    $app.innerHTML = `
      <h1>About this tracker</h1>
      <div class="brief">
        <p><strong>What it is.</strong> A daily, automated synthesis of what credible forecasters, public polls, prediction markets, and reporting say about the 2026 midterm elections, with movement over time. A cloud routine refreshes it every morning.</p>
        <p><strong>What it is not.</strong> A forecast. Nothing here is a prediction by Jake or by the AI that assembles it. The consensus rating is the median of the forecasters' published calls; market numbers are prices, not probabilities we endorse.</p>
        <h2>Sources</h2>
        <ul>
          <li><strong>Race ratings:</strong> ${DATA.sources.forecasters.map(esc).join(", ")}, as compiled with update dates on Wikipedia's <a href="${esc(DATA.sources.ratings[0])}" target="_blank" rel="noopener">Senate</a> and <a href="${esc(DATA.sources.ratings[1])}" target="_blank" rel="noopener">House</a> ratings tables.</li>
          <li><strong>Polls:</strong> the <a href="https://votehub.com" target="_blank" rel="noopener">VoteHub</a> public polling feed. Averages are simple means over a 21-day window (45 days when fewer than two polls), excluding internal polls; partisan sponsors are labeled.</li>
          <li><strong>Markets:</strong> <a href="https://polymarket.com" target="_blank" rel="noopener">Polymarket</a> chamber-control and balance-of-power markets, shown as implied probabilities and never mixed with polls.</li>
          <li><strong>Reporting and commentary:</strong> RSS feeds from outlets and podcasts across the political spectrum, each tagged by lean, plus Google News searches per competitive race. Headlines are linked, never reproduced.</li>
        </ul>
        <h2>Rules</h2>
        <ul>
          <li>Every claim in a brief links to a source. Polls carry their dates and pollsters. Opinions are labeled as opinions, with the outlet's lean.</li>
          <li>Each brief reflects at least one left-leaning and one right-leaning perspective when commentary is included.</li>
          <li>Paraphrase only. No paywalled text is stored or shown.</li>
          <li>If a source fails, the brief says so rather than working around it.</li>
        </ul>
        <h2>Today's coverage</h2>
        <p>${c.succeeded ?? "?"} of ${c.attempted ?? "?"} sources reported${c.failed_sources && c.failed_sources.length ? `; failed: ${c.failed_sources.map(esc).join(", ")}` : "."}</p>
        <p class="meta">Code and data: <a href="https://github.com/jakeworcester-byte/political-tracker" target="_blank" rel="noopener">github.com/jakeworcester-byte/political-tracker</a>. Built with Claude Code.</p>
      </div>`;
  }

  window.addEventListener("hashchange", route);
  load();
})();
