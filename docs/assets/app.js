/* Jake's Political Tracker: static front end over docs/data/*.json. No build step, no framework. */
(function () {
  "use strict";
  const $app = document.getElementById("app");
  const $tabs = document.getElementById("tabs");
  const $footMeta = document.getElementById("foot-meta");
  let DATA = null, HISTORY = null;
  const raceCache = {};
  // Data lives in the tracker repo. When this page is served from the project path (or locally), read it
  // relatively; when served from the user site root (jakeworcester-byte.github.io/), read the raw files on main.
  const ON_PROJECT_PATH = location.pathname.indexOf("/political-tracker") === 0;
  const ON_GITHUB_USER_SITE = /\.github\.io$/.test(location.hostname) && !ON_PROJECT_PATH;
  const DATA_BASE = ON_GITHUB_USER_SITE ? "https://raw.githubusercontent.com/jakeworcester-byte/political-tracker/main/docs/data/" : "data/";
  const dataUrl = p => DATA_BASE + p;

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
      const r = await fetch(dataUrl("latest.json"), { cache: "no-store" });
      if (!r.ok) throw new Error(`latest.json ${r.status}`);
      DATA = await r.json();
      fetch(dataUrl("history.json"), { cache: "no-store" }).then(x => x.ok ? x.json() : []).then(h => { HISTORY = h; if (location.hash.startsWith("#national") || location.hash === "#pre") route(); }).catch(() => {});
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
      case "pre": setTab("pre"); return renderPreElection();
      case "senate": setTab("pre"); return renderChamber("Senate");
      case "house": setTab("pre"); return renderChamber("House");
      case "race": setTab("pre"); return renderRace(arg);
      case "national": setTab("pre"); return renderNational();
      case "early": setTab("pre"); return renderEarlyVote();
      case "results": setTab("results"); return renderResults();
      case "about": setTab("about"); return renderAbout();
      default: setTab("overview"); return renderOverview();
    }
  }

  // ------------------------------------------------------------------ overview: results dashboard
  async function loadResults() {
    if (!RESULTS) { try { const r = await fetch(dataUrl("results.json"), { cache: "no-store" }); RESULTS = r.ok ? await r.json() : null; } catch (e) { RESULTS = null; } }
    if (!SCORE) { try { const r = await fetch(dataUrl("scorecard.json"), { cache: "no-store" }); SCORE = r.ok ? await r.json() : null; } catch (e) { SCORE = null; } }
  }

  // Parliament-style arc: one dot per seat, groups fill left to right as wedges.
  function hemicycle(groups, total, rows, aria) {
    const cx = 100, cy = 100, R1 = 96, R0 = rows > 6 ? 34 : 44;
    const radii = Array.from({ length: rows }, (_, i) => R0 + (R1 - R0) * i / (rows - 1));
    const sumR = radii.reduce((a, b) => a + b, 0);
    const counts = radii.map(r => Math.floor(total * r / sumR));
    let rest = total - counts.reduce((a, b) => a + b, 0);
    for (let i = rows - 1; rest > 0; i = (i - 1 + rows) % rows, rest--) counts[i]++;
    const pts = [];
    radii.forEach((r, i) => { const n = counts[i]; for (let j = 0; j < n; j++) { const a = Math.PI * (1 - (n === 1 ? 0.5 : j / (n - 1))); pts.push({ x: cx + r * Math.cos(a), y: cy - r * Math.sin(a), a }); } });
    pts.sort((p, q) => q.a - p.a || p.y - q.y);
    const gap = Math.min((R1 - R0) / (rows - 1), Math.PI * R0 / (counts[0] - 1));
    const dr = (gap * 0.42).toFixed(2);
    let k = 0;
    const g = groups.map(gr => { const dots = pts.slice(k, k + gr.n); k += gr.n; return `<g class="${gr.cls}"><title>${esc(gr.label)}: ${gr.n}</title>${dots.map(p => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${dr}"/>`).join("")}</g>`; }).join("");
    return `<svg class="hemi" viewBox="0 -1 200 107" role="img" aria-label="${esc(aria)}">${g}<line x1="100" x2="100" y1="0" y2="6" class="maj"/></svg>`;
  }

  // Tile map: one square per state, roughly in geographic position.
  const TILES = { AK: [0, 0], ME: [10, 0], VT: [9, 1], NH: [10, 1], WA: [0, 2], ID: [1, 2], MT: [2, 2], ND: [3, 2], MN: [4, 2], IL: [5, 2], WI: [6, 2], MI: [7, 2], NY: [8, 2], RI: [9, 2], MA: [10, 2],
    OR: [0, 3], NV: [1, 3], WY: [2, 3], SD: [3, 3], IA: [4, 3], IN: [5, 3], OH: [6, 3], PA: [7, 3], NJ: [8, 3], CT: [9, 3],
    CA: [0, 4], UT: [1, 4], CO: [2, 4], NE: [3, 4], MO: [4, 4], KY: [5, 4], WV: [6, 4], VA: [7, 4], MD: [8, 4], DE: [9, 4],
    AZ: [1, 5], NM: [2, 5], KS: [3, 5], AR: [4, 5], TN: [5, 5], NC: [6, 5], SC: [7, 5], OK: [3, 6], LA: [4, 6], MS: [5, 6], AL: [6, 6], GA: [7, 6], HI: [0, 7], TX: [3, 7], FL: [8, 7] };
  function govClass(v) {
    if (!v) return "none";
    if (!v.up) return v.party === "D" ? "d-old" : v.party === "R" ? "r-old" : "o-old";
    if (!v.called || !v.winner) return "unc";
    return v.winner.party === "D" ? "d-new" : v.winner.party === "R" ? "r-new" : "o-new";
  }
  function govLine(abbr, v) {
    if (!v) return "";
    const rating = v.final_rating ? ` Final pre-election rating: ${v.final_rating}.` : "";
    if (!v.up) return `<b>${esc(v.state)}</b>: no race this year. Gov. ${esc(v.governor)} (${esc(v.party)}).`;
    if (!v.called || !v.winner) return `<b>${esc(v.state)}</b>: not yet called.${v.leader && v.margin_pts != null ? ` ${esc(v.leader.name)} (${esc(v.leader.party)}) leads by ${v.margin_pts} points in the results table.` : ""}${rating}`;
    return `<b>${esc(v.state)}</b>: ${esc(v.winner.name)} (${esc(v.winner.party)}) won${v.flip ? `, a flip from ${esc(v.incumbent_party)}` : ""}.${rating}`;
  }
  function govMap(G) {
    const st = G.states || {};
    const cells = Object.entries(TILES).map(([a, [c, r]]) => {
      const v = st[a]; const cls = govClass(v);
      return `<g class="tile ${cls}${v && v.flip ? " flip" : ""}" data-st="${a}" tabindex="0"><title>${govLine(a, v).replace(/<[^>]+>/g, "")}</title><rect x="${c * 20 + 1}" y="${r * 20 + 1}" width="18" height="18" rx="3"/><text x="${c * 20 + 10}" y="${r * 20 + 13.5}">${a}</text></g>`;
    }).join("");
    return `<svg class="tiles" viewBox="0 0 220 160" role="img" aria-label="Governors by state">${cells}</svg>`;
  }

  const SNAP_LABEL = s => { const d = new Date(s.slice(0, 10) + "T12:00:00"); return `${d.toLocaleDateString("en-US", { weekday: "short" })} ${s.endsWith("pm") ? "pm" : "am"}`; };
  const gainCount = (races, chamber) => { const g = { D: 0, R: 0 }; Object.values(races).forEach(r => { if (r.chamber !== chamber || !r.called) return; if (/Democratic\S* gain/i.test(r.status_text)) g.D++; else if (/Republican\S* gain/i.test(r.status_text)) g.R++; }); return g; };

  function projRows(P, actualD, uncalled) {
    const vals = P.forecasters.map(f => f.D).concat([actualD, P.majority]);
    const lo = Math.floor((Math.min(...vals) - 4) / 5) * 5, hi = Math.ceil((Math.max(...vals) + 4) / 5) * 5;
    const X = v => `${(100 * (v - lo) / (hi - lo)).toFixed(2)}%`;
    return `<div class="proj">
      ${P.forecasters.map(f => `<div class="proj-row"><div class="proj-lab">${esc(f.forecaster)}</div><div class="proj-track"><span class="proj-maj" style="left:${X(P.majority)}"></span><span class="proj-act" style="left:${X(actualD)}"></span><span class="proj-dot" style="left:${X(f.D)}" title="${esc(f.forecaster)}: D ${f.D}, R ${f.R}${f.tossups != null ? `, ${f.tossups} tossups` : ""}"></span><span class="proj-val" style="left:${X(f.D)}">${f.D}</span></div></div>`).join("")}
      <div class="proj-row proj-axis"><div class="proj-lab"></div><div class="proj-track"><span class="act" style="left:${X(actualD)}">${actualD} won${uncalled ? ` (${uncalled} not called)` : ""}</span><span class="maj" style="left:${X(P.majority)}">${P.majority} for majority</span></div></div>
    </div>`;
  }

  async function renderOverview() {
    $app.innerHTML = `<h1>Results</h1><div class="loading">Loading…</div>`;
    await loadResults();
    if (location.hash && location.hash !== "#overview") return;
    if (!RESULTS) { $app.innerHTML = `<h1>Results</h1><div class="callout">Results are not available right now. <a href="#pre">See the pre-election picture →</a></div>`; return; }
    const R = RESULTS, S = SCORE, h = R.house, s = R.senate, G = R.governors, races = R.races || {}, b = DATA.brief;
    const ss = s.seats_secured || { D: 0, R: 0, I: 0 }, nu = s.not_up || { D: 0, R: 0 };
    const sUnc = s.races - s.called, hUnc = h.races - h.called;
    const sg = gainCount(races, "Senate"), hg = gainCount(races, "House");
    const sBefore = { D: nu.D, R: nu.R };
    Object.values(races).forEach(r => { if (r.chamber === "Senate") { if (r.incumbent_party === "R") sBefore.R++; else if (r.incumbent_party === "D" || r.incumbent_party === "I") sBefore.D++; } });
    const net = g => g.D === g.R ? "no net change" : g.D > g.R ? `D net +${g.D - g.R}` : `R net +${g.R - g.D}`;
    const senateSvg = hemicycle([
      { n: nu.D, cls: "d-old", label: "Democrats, seats not up this year" }, { n: s.won.D + (s.won.I || 0), cls: "d-new", label: "Democrats, won Nov. 3" },
      { n: sUnc, cls: "unc", label: "Not yet called" },
      { n: s.won.R, cls: "r-new", label: "Republicans, won Nov. 3" }, { n: nu.R, cls: "r-old", label: "Republicans, seats not up this year" }], 100, 5, `Senate: Democrats ${ss.D}, Republicans ${ss.R}, ${sUnc} not called`);
    const houseSvg = hemicycle([
      { n: h.won.D, cls: "d-new", label: "Democrats, won" }, { n: hUnc, cls: "unc", label: "Not yet called" },
      { n: h.won.R + (h.won.I || 0) + (h.won.Other || 0), cls: "r-new", label: "Republicans, won" }], 435, 12, `House: Democrats ${h.won.D}, Republicans ${h.won.R}, ${hUnc} not called`);
    const gs = G ? G.seats_secured : null;
    const P = S && S.projections;
    const tl = (R.call_timeline || {}).snapshots || [];
    const tlMax = Math.max(1, ...tl.map(t => t.new));
    const unc = (R.uncalled_competitive || []).map(k => races[k]).filter(Boolean);
    const govUnc = G ? Object.values(G.states).filter(v => v.up && !v.called) : [];
    const upsets = S ? (S.upsets || []).concat((S.governors && S.governors.upsets) || []) : [];
    const chamberOrder = { Senate: 0, Governor: 1, House: 2 };
    upsets.sort((a, b2) => chamberOrder[a.chamber] - chamberOrder[b2.chamber]);
    const legend = (items) => `<div class="legend">${items.map(([c, l]) => `<span><i class="sw ${c}"></i>${l}</span>`).join("")}</div>`;

    $app.innerHTML = `
      <h1>2026 midterm results</h1>
      <p class="sub">Updated ${new Date(R.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}. Races count as called when Wikipedia's results tables mark them, following AP and the networks. We never call races ourselves. <a href="#results">Race-by-race detail →</a></p>

      <div class="hero3">
        <div class="card hero">
          <div class="label">Senate · 51 for control</div>
          <div class="hero-nums"><span class="dn">D ${ss.D}</span><span class="rn">R ${ss.R}</span></div>
          ${senateSvg}
          ${legend([["pair-new", "Won Nov. 3"], ["pair-old", "Not up this year"], ["unc", `Not called (${sUnc})`]])}
          <div class="note">Before: D ${sBefore.D} · R ${sBefore.R}. Seats flipped: ${sg.D} to D, ${sg.R} to R.</div>
        </div>
        <div class="card hero">
          <div class="label">House · 218 for control</div>
          <div class="hero-nums"><span class="dn">D ${h.won.D}</span><span class="rn">R ${h.won.R}</span></div>
          ${houseSvg}
          ${legend([["d-new", "Democrat"], ["r-new", "Republican"], ["unc", `Not called (${hUnc})`]])}
          <div class="note">Seats flipped: ${hg.D} to D, ${hg.R} to R (${net(hg)}), per the results table.</div>
        </div>
        <div class="card hero">
          <div class="label">Governors · 50 states</div>
          ${gs ? `<div class="hero-nums"><span class="dn">D ${gs.D}</span><span class="rn">R ${gs.R}</span></div>
          ${govMap(G)}
          ${legend([["pair-new", "Won Nov. 3"], ["pair-old", "Not up this year"], ["unc", `Not called (${G.uncalled})`], ["flip", "Flipped"]])}
          <div class="note" id="gov-detail">Before: D ${G.before.D} · R ${G.before.R}. ${G.flips.length} flipped. Tap a state for detail.</div>` : `<div class="meta">Governor results are not available in this update.</div>`}
        </div>
      </div>

      ${P ? `<h2>Forecasts vs. results</h2>
      <p class="sub">Each dot is the number of seats a forecaster rated at least Tilt Democratic before Election Day. The solid line is the seats Democrats have won; the dashed line is the majority mark.</p>
      <div class="two">
        <div class="card"><div class="label">House: Democratic seats</div>${projRows(P.house, h.won.D, hUnc)}</div>
        <div class="card"><div class="label">Senate: Democratic seats (incl. seats not up)</div>${projRows(P.senate, ss.D, sUnc)}</div>
      </div>
      ${S.markets ? `<p class="meta">Final pre-election market prices (Polymarket): ${S.markets.house.final_price_D}% for Democratic House control and ${S.markets.senate.final_price_D}% for Democratic Senate control. Control decided so far: House ${S.markets.house.control_decided || "not yet"}, Senate ${S.markets.senate.control_decided || "not yet"}.</p>` : ""}` : ""}

      ${S && S.by_rating ? `<h2>What each rating turned out to mean</h2>
      <p class="sub">House and Senate races grouped by the forecasters' final consensus rating, and who won them.</p>
      <div class="card">${S.by_rating.map(x => { const n = x.D + x.R + x.Other + x.uncalled; const w = v => `${(100 * v / n).toFixed(1)}%`; return `<div class="rb-row"><div class="rb-lab">${pill(x.rating, "sm")}</div><div class="bar rb-bar" title="${esc(x.rating)}: ${x.D} D, ${x.R} R${x.uncalled ? `, ${x.uncalled} not called` : ""}"><span class="d" style="width:${w(x.D)}"></span><span class="unc" style="width:${w(x.uncalled + x.Other)}"></span><span class="r" style="width:${w(x.R)}"></span></div><div class="rb-num">${x.D} D · ${x.R} R${x.uncalled ? ` · ${x.uncalled} out` : ""}</div></div>`; }).join("")}
      ${legend([["d-new", "Democrat won"], ["r-new", "Republican won"], ["unc", "Not called"]])}</div>` : ""}

      ${upsets.length ? `<h2>Upsets: the favored party lost (${upsets.length})</h2>
      <div class="table-scroll"><table class="ratings"><thead><tr><th>Race</th><th>Final rating</th><th>Winner</th></tr></thead>
      <tbody>${upsets.map(u => `<tr><td>${esc(u.name)}<div class="meta">${esc(u.chamber)}</div></td><td>${pill(u.final_rating, "sm")}</td><td>${esc(u.winner)} ${partyPill(u.party)}</td></tr>`).join("")}</tbody></table></div>
      <p class="meta">An upset here means the race was rated Tilt or stronger for one party and the other party won. Races the consensus rated Tossup are not counted.</p>` : ""}

      ${S && S.forecasters ? `<h2>How each forecaster did</h2>
      <div class="table-scroll"><table class="ratings"><thead><tr><th>Forecaster</th><th>Right</th><th>Wrong</th><th>Tossups</th><th>Accuracy</th></tr></thead>
      <tbody>${S.forecasters.map(f => `<tr><td>${esc(f.forecaster)}</td><td>${f.right}</td><td>${f.wrong}</td><td>${f.tossups}</td><td><b>${f.accuracy_pct == null ? "n/a" : f.accuracy_pct + "%"}</b></td></tr>`).join("")}</tbody></table></div>
      <p class="meta">House and Senate races called so far. Accuracy is right ÷ (right + wrong); Tossup ratings are set aside. ${S.governors ? `Governors, consensus rating: ${S.governors.right} right, ${S.governors.wrong} wrong, ${S.governors.tossup} Tossup.` : ""}</p>` : ""}

      ${tl.length ? `<h2>How the calls came in</h2>
      <p class="sub">Competitive House and Senate races newly marked called at each update: ${tl[tl.length - 1].competitive_called} of ${R.call_timeline.competitive_total} called so far.</p>
      <div class="card"><div class="cols">${tl.map(t => `<div class="col" title="${SNAP_LABEL(t.snapshot)}: ${t.new} newly called, ${t.competitive_called} total"><div class="col-n">${t.new}</div><div class="col-bar" style="height:${Math.max(2, 100 * t.new / tlMax)}%"></div><div class="col-lab">${SNAP_LABEL(t.snapshot)}</div></div>`).join("")}</div></div>` : ""}

      <h2>Still out</h2>
      <div class="card"><ul class="list">${unc.map(r => `<li><div class="item-title">${esc(r.name)} ${r.final_rating ? pill(r.final_rating, "sm") : ""}</div><div class="item-meta">${esc(r.chamber)}${r.margin_pts != null && r.leader ? ` · ${esc(r.leader.name)} (${esc(r.leader.party)}) leads by ${r.margin_pts} pts in the table` : ""}</div></li>`).join("")}${govUnc.map(v => `<li><div class="item-title">${esc(v.state)} governor ${v.final_rating ? pill(v.final_rating, "sm") : ""}</div><div class="item-meta">Governor${v.leader && v.margin_pts != null ? ` · ${esc(v.leader.name)} (${esc(v.leader.party)}) leads by ${v.margin_pts} pts in the table` : ""}</div></li>`).join("") || '<li class="meta">Every competitive race has been called.</li>'}</ul>
      <p class="meta" style="margin:8px 0 0"><a href="#results">Latest reporting on each race →</a></p></div>

      ${b ? `<h2>Latest brief</h2><div class="brief">${md(b.markdown.split(/\n## /).slice(0, 2).join("\n## "))}<p><a class="btn" href="#brief">Read the full brief →</a></p></div>` : ""}

      <div class="callout" style="margin-top:18px">Looking for the final ratings, polls, markets, and early vote? <a href="#pre">See the pre-election picture →</a></div>
      <p class="meta" style="margin-top:12px">Sources: <a href="${esc(R.sources.house)}" target="_blank" rel="noopener">House</a>, <a href="${esc(R.sources.senate)}" target="_blank" rel="noopener">Senate</a>${G && G.sources.results ? `, <a href="${esc(G.sources.results)}" target="_blank" rel="noopener">governor</a>` : ""} results tables on Wikipedia${G && G.sources.not_up ? `; governors not up this year from Wikipedia's <a href="${esc(G.sources.not_up)}" target="_blank" rel="noopener">list of current governors</a>` : ""}. Forecaster ratings as published before Election Day.</p>`;

    const det = document.getElementById("gov-detail");
    if (det && G) $app.querySelectorAll(".tiles .tile").forEach(t => {
      const show = () => { $app.querySelectorAll(".tiles .tile.sel").forEach(x => x.classList.remove("sel")); t.classList.add("sel"); det.innerHTML = govLine(t.dataset.st, G.states[t.dataset.st]); };
      t.addEventListener("click", show); t.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(); } });
    });
  }

  // ------------------------------------------------------------------ pre-election overview (archive)
  function controlCard(label, m, extra) {
    if (!m || m.D == null) return `<div class="card"><div class="label">${label}</div><div class="big">n/a</div><div class="note">market unavailable</div></div>`;
    const lead = m.D >= 50 ? `D ${pct(m.D)}` : `R ${pct(m.R)}`;
    return `<div class="card"><div class="label">${label}</div><div class="big" style="color:${m.D >= 50 ? "var(--dem)" : "var(--rep)"}">${lead}</div>
      <div class="note">${extra || ""}<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.source)}</a> implied probability</div></div>`;
  }

  const PRE_NAV = () => `<div class="filters subnav"><a href="#pre" class="${location.hash === "#pre" ? "on" : ""}">Summary</a><a href="#senate" class="${location.hash === "#senate" ? "on" : ""}">Senate</a><a href="#house" class="${location.hash === "#house" ? "on" : ""}">House</a><a href="#national" class="${location.hash === "#national" ? "on" : ""}">Electorate</a><a href="#early" class="${location.hash === "#early" ? "on" : ""}">Early vote</a></div>`;
  function renderPreElection() {
    const n = DATA.national, ov = DATA.overview, s = ov.senate, b = DATA.brief;
    const senate = DATA.races.filter(r => r.chamber === "Senate");
    const house = DATA.races.filter(r => r.chamber === "House");
    const sb = bucketCounts(senate), hb = bucketCounts(house);
    const movers = DATA.movers || [];
    const watch = DATA.races.filter(r => r.tracked).sort((a, b) => Math.abs(a.consensus_score) - Math.abs(b.consensus_score) || (b.signal_count - a.signal_count)).slice(0, 6);
    $app.innerHTML = `
      <h1>Before the election</h1>
      ${PRE_NAV()}
      <p class="sub">Archive of the pre-election picture: the forecasters' final published ratings and the last market and poll readings the tracker collected. <a href="#overview">Results dashboard →</a></p>
      <div class="cards">
        ${controlCard("Senate control", n.markets.senate)}
        ${controlCard("House control", n.markets.house)}
        <div class="card"><div class="label">Generic ballot</div><div class="big" style="color:${(n.generic_ballot.average_margin || 0) < 0 ? "var(--dem)" : "var(--rep)"}">${esc(n.generic_ballot.average_label)}</div><div class="note">${gbBasis(n.generic_ballot)} · <a href="#national">details</a></div></div>
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

      <h2>What moved</h2>
      ${movers.length ? `<ul class="list">${movers.map(m => `<li><div class="item-title">${esc(m.name)} <span class="pill gray sm">${esc(m.type)}</span></div><div class="item-meta">${esc(m.text)} · ${m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.source)}</a>` : esc(m.source)}</div></li>`).join("")}</ul>` : `<div class="callout">No rating, polling-average, or market change crossed a reporting threshold since the last run.</div>`}

      <h2>Races to watch (final ratings)</h2>
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
      ${PRE_NAV()}
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
      try { const resp = await fetch(dataUrl(`races/${key}.json`), { cache: "no-store" }); d = resp.ok ? await resp.json() : null; raceCache[key] = d; } catch (e) { d = null; }
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
  function gbBasis(gb) {
    return gb.source === "aggregates" ? `avg of ${gb.n_aggregators} aggregators` : `avg of ${gb.n_polls} polls${gb.polls_stale ? " (stale)" : ""}`;
  }

  function renderNational() {
    const n = DATA.national, gb = n.generic_ballot, ap = n.approval, m = n.markets;
    const hist = (HISTORY || []).filter(h => h.generic_ballot != null);
    const mh = (HISTORY || []).filter(h => h.markets && h.markets.senate != null);
    $app.innerHTML = `
      <h1>Where the electorate was</h1>
      ${PRE_NAV()}
      <p class="sub">Generic ballot, presidential approval, and prediction markets. Generic ballot from the aggregators compiled on Wikipedia; polls from the VoteHub public feed; markets from Polymarket.</p>
      <div class="cards">
        <div class="card"><div class="label">Generic ballot</div><div class="big" style="color:${(gb.average_margin || 0) < 0 ? "var(--dem)" : "var(--rep)"}">${esc(gb.average_label)}</div><div class="note">${gbBasis(gb)}</div></div>
        <div class="card"><div class="label">Approve</div><div class="big">${ap.approve == null ? "n/a" : ap.approve.toFixed(1) + "%"}</div><div class="note">${ap.n_polls} polls</div></div>
        <div class="card"><div class="label">Disapprove</div><div class="big">${ap.disapprove == null ? "n/a" : ap.disapprove.toFixed(1) + "%"}</div><div class="note">net ${ap.net == null ? "" : (ap.net > 0 ? "+" : "") + ap.net.toFixed(1)}</div></div>
        ${controlCard("Senate control", m.senate)}
        ${controlCard("House control", m.house)}
      </div>
      ${(gb.aggregates || []).length ? `<h2>Generic ballot aggregators</h2><div class="card"><ul class="list">${gb.aggregates.map(a => `<li><div class="item-title">${esc(a.aggregator)} <span class="pill ${a.margin < 0 ? "D" : a.margin > 0 ? "R" : "T"} sm">${margin(a.margin)}</span></div><div class="item-meta">D ${a.dem}% / R ${a.rep}%${a.updated ? ` · updated ${fmtDate(a.updated)}` : ""}</div></li>`).join("")}</ul><p class="meta"><a href="${esc(gb.aggregates_url)}" target="_blank" rel="noopener">Wikipedia aggregate table</a></p></div>` : ""}
      ${hist.length > 1 ? `<h2>Generic ballot average over time</h2><div class="card">${sparkline(hist.map(h => ({ x: h.date, y: h.generic_ballot })), true)}</div>` : ""}
      ${mh.length > 1 ? `<h2>Market odds of Democratic Senate control</h2><div class="card">${sparkline(mh.map(h => ({ x: h.date, y: h.markets.senate })), false)}</div>` : ""}
      ${m.balance ? `<h2>Balance of power (Polymarket)</h2><div class="card"><ul class="list">${Object.entries(m.balance.combos).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<li><div class="item-title">${esc(k)} <span class="pill gray sm">${v}%</span></div></li>`).join("")}</ul><p class="meta"><a href="${esc(m.balance.url)}" target="_blank" rel="noopener">Market page</a> · implied probabilities, labeled as market prices and never averaged with polls.</p></div>` : ""}
      <div class="two">
        <div><h2>Recent generic ballot polls</h2><div class="card">${gb.polls_stale ? `<p class="meta">The VoteHub feed has no generic ballot poll newer than ${gb.latest_poll_date ? fmtDate(gb.latest_poll_date) : "the last 120 days"}.</p>` : ""}<ul class="list">${gb.polls.map(p => `<li><div class="item-title">${esc(p.pollster)} <span class="pill ${p.margin < 0 ? "D" : p.margin > 0 ? "R" : "T"} sm">${margin(p.margin)}</span></div><div class="item-meta">${fmtDate(p.end_date)} · n=${p.n || "?"} ${esc(p.population || "")}${(p.sponsors || []).length ? ` · ${esc(p.sponsors.join(", "))}` : ""}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""}</div></li>`).join("")}</ul></div></div>
        <div><h2>Recent approval polls</h2><div class="card"><ul class="list">${ap.polls.map(p => `<li><div class="item-title">${esc(p.pollster)} <span class="pill gray sm">${p.approve}/${p.disapprove}</span></div><div class="item-meta">${fmtDate(p.end_date)} · n=${p.n || "?"} ${esc(p.population || "")}${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ""}</div></li>`).join("")}</ul></div></div>
      </div>
      <h2>National reporting and commentary</h2>
      <div class="card"><ul class="list">${(DATA.national_signals || []).slice(0, 25).map(s => `<li><div class="item-title"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a></div><div class="item-meta">${esc(s.source)}${s.lean && s.lean !== "unrated" ? ` · <span class="pill gray sm">${esc(s.lean)}</span>` : ""} · ${fmtDate(s.date)} · ${esc(s.type)}</div></li>`).join("")}</ul></div>`;
  }


  // ------------------------------------------------------------------ early vote
  let EARLY = null;
  const signed = v => v == null ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(1)}`;
  const mlabel = m => m == null ? "n/a" : Math.abs(m) < 0.05 ? "Even" : `${m > 0 ? "D" : "R"}+${Math.abs(m).toFixed(1)}`;
  function shareBar(label, sh) {
    if (!sh || sh.D == null) return "";
    const o = sh.Other != null ? sh.Other : Math.max(0, 100 - sh.D - sh.R);
    return `<div class="ev-row"><div class="ev-lab">${label}</div><div class="bar ev-bar"><span class="d" style="width:${sh.D}%"></span><span class="t ev-o" style="width:${o}%"></span><span class="r" style="width:${sh.R}%"></span></div><div class="ev-nums"><b style="color:var(--dem)">D ${sh.D.toFixed(1)}</b> · <b style="color:var(--rep)">R ${sh.R.toFixed(1)}</b> · other ${o.toFixed(1)}</div></div>`;
  }
  function evCard(s, hist) {
    const series = (hist || []).filter(h => h.ballot_margin != null);
    const prior = s.prior || {};
    const facts = [
      s.total ? `<div><div class="label">Ballots</div><div class="ev-big">${s.total.toLocaleString()}</div></div>` : "",
      s.overperformance != null ? `<div><div class="label">D–R margin vs registration</div><div class="ev-big" style="color:${s.overperformance >= 0 ? "var(--dem)" : "var(--rep)"}">${signed(s.overperformance)}</div><div class="note">${mlabel(s.ballot_margin)} among ballots vs ${mlabel(s.registration_margin)} registered</div></div>` : "",
      s.margin_change_vs_prior != null ? `<div><div class="label">Margin vs ${esc(prior.cycle)} same point</div><div class="ev-big" style="color:${s.margin_change_vs_prior >= 0 ? "var(--dem)" : "var(--rep)"}">${signed(s.margin_change_vs_prior)}</div><div class="note">positive = more Democratic</div></div>` : "",
      s.pace_vs_prior_pct != null ? `<div><div class="label">Volume vs ${esc(prior.cycle)} same point</div><div class="ev-big">${Math.round(s.pace_vs_prior_pct)}%</div></div>` : "",
      s.share_of_2022_turnout_pct != null ? `<div><div class="label">Share of total 2022 turnout</div><div class="ev-big">${s.share_of_2022_turnout_pct}%</div><div class="note">whole-election total, not same point</div></div>` : ""
    ].join("");
    return `<div class="card ev-card${s.stale ? " ev-stale" : ""}">
      <div class="ev-head"><div class="item-title">${esc(s.name || s.state)}</div><div class="meta">${esc(s.measure || "early ballots")} · as of ${fmtDate(s.as_of)}${s.stale ? ' · <span class="pill gray sm">not updated today</span>' : ""}${s.automated ? ' · <span class="pill gray sm">automated</span>' : ""}</div></div>
      ${shareBar("Early ballots", s.ballots_share)}
      ${shareBar("Registration", s.registration_share)}
      ${prior.share ? shareBar(`${esc(prior.cycle)} same point`, prior.share) : ""}
      ${facts ? `<div class="ev-facts">${facts}</div>` : ""}
      ${s.day_change ? `<p class="meta">Since last report: ${esc(s.day_change)}</p>` : ""}
      ${series.length > 1 ? `<div class="label" style="margin-top:8px">D–R margin among early ballots</div>${sparkline(series.map(h => ({ x: h.as_of || h.run_date, y: -h.ballot_margin })), true)}` : ""}
      ${s.note ? `<p class="meta">${esc(s.note)}</p>` : ""}
      <p class="meta"><a href="${esc(s.source_url)}" target="_blank" rel="noopener">${esc(s.source || "source")}</a>${s.registration_source_url ? ` · registration: <a href="${esc(s.registration_source_url)}" target="_blank" rel="noopener">${esc(s.registration_source || "source")}</a>${s.registration_as_of ? `, ${fmtDate(s.registration_as_of)}` : ""}` : ""}</p>
    </div>`;
  }
  async function renderEarlyVote() {
    $app.innerHTML = `<h1>Early vote</h1><div class="loading">Loading…</div>`;
    if (!EARLY) { try { const r = await fetch(dataUrl("early_vote.json"), { cache: "no-store" }); EARLY = r.ok ? await r.json() : null; } catch (e) { EARLY = null; } }
    if (!location.hash.startsWith("#early")) return;
    if (!EARLY || !(EARLY.states || []).length) { $app.innerHTML = `<h1>Early vote</h1><div class="callout">No early vote data yet. It updates each morning during early voting.</div>`; return; }
    $app.innerHTML = `
      <h1>Early vote</h1>
      ${PRE_NAV()}
      <p class="sub">Early ballots by party registration, compared with each state's registration and, where reported, the same point in a prior cycle. Updated each morning through Election Day.</p>
      <div class="callout"><strong>Read with care.</strong> ${esc(EARLY.caveat)} Mail ballots have skewed Democratic since 2020, so the change versus a prior cycle says more about enthusiasm than the raw gap.</div>
      <div class="ev-grid">${EARLY.states.map(s => evCard(s, (EARLY.history || {})[s.state])).join("")}</div>
      ${(EARLY.problems || []).length ? `<h2>Not available today</h2><div class="card"><ul class="list">${EARLY.problems.map(p => `<li class="meta">${esc(p)}</li>`).join("")}</ul></div>` : ""}
      <p class="meta" style="margin-top:12px">Updated ${new Date(EARLY.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}.</p>`;
  }

  // ------------------------------------------------------------------ results (post-election)
  let RESULTS = null, SCORE = null;
  const partyPill = p => `<span class="pill ${p === "D" ? "D" : p === "R" ? "R" : "T"} sm">${esc(p)}</span>`;
  const flagBadge = r => r && r.rated_safe_now_close ? ` <span class="flag-badge" title="Rated ${esc(r.final_rating || "unrated (safe)")} before the election; the count is now within 5 points">Rated safe</span>` : "";
  function resultLine(r) {
    const c = [...(r.candidates || [])].filter(x => x.pct != null).sort((a, b) => b.pct - a.pct);
    if (c.length >= 2) return `${esc(c[0].name)} ${partyPill(c[0].party)} ${c[0].pct.toFixed(1)}% · ${esc(c[1].name)} ${partyPill(c[1].party)} ${c[1].pct.toFixed(1)}%`;
    return `<span class="meta">No vote shares in the results table yet</span>`;
  }
  async function renderResults() {
    $app.innerHTML = `<h1>Election results</h1><div class="loading">Loading…</div>`;
    if (!RESULTS) { try { const r = await fetch(dataUrl("results.json"), { cache: "no-store" }); RESULTS = r.ok ? await r.json() : null; } catch (e) { RESULTS = null; } }
    if (!SCORE) { try { const r = await fetch(dataUrl("scorecard.json"), { cache: "no-store" }); SCORE = r.ok ? await r.json() : null; } catch (e) { SCORE = null; } }
    if (!location.hash.startsWith("#results")) return;
    if (!RESULTS) { $app.innerHTML = `<h1>Election results</h1><div class="callout">Results start appearing here the morning after polls close on November 3.</div>`; return; }
    const R = RESULTS, h = R.house, s = R.senate, races = R.races || {}, news = R.news || {};
    const ss = s.seats_secured || { D: "?", R: "?" };
    const newly = (R.newly_called || []).filter(k => races[k] && (races[k].competitive || races[k].rated_safe_now_close));
    const flagged = R.rated_safe_now_close || [];
    const unc = R.uncalled_competitive || [];
    const firstLink = k => {
      const rc = races[k] && races[k].reported_call;
      if (rc) { const x = rc.sources[0] || {}; return `<span class="pill sm ${rc.winner.party === "D" ? "D" : rc.winner.party === "R" ? "R" : "T"}">Reported: ${esc(rc.winner.name)}</span> <a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.outlet || "source")}</a>`; }
      const it = (news[k] || [])[0]; return it ? `<a href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.outlet || "latest")}</a>` : ""; };
    $app.innerHTML = `
      <h1>Election results</h1>
      <p class="sub">Updated ${new Date(R.generated_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}. A race counts as called when Wikipedia's results table marks it, following AP and network calls. Because the table lags, races that news outlets report as called (a projection cited, or two outlets agreeing) are shown separately as "reported." We never call races ourselves.</p>
      <div class="cards">
        <div class="card"><div class="label">House (218 for majority)</div><div class="big">D ${h.won.D} · R ${h.won.R}</div><div class="note">${h.called} of ${h.races} marked called in the results table${h.reported_count ? `; with ${h.reported_count} more reported called in news: D ${h.won_incl_reported.D} · R ${h.won_incl_reported.R}` : ""}</div></div>
        <div class="card"><div class="label">Senate (51 for majority)</div><div class="big">D ${ss.D} · R ${ss.R}</div><div class="note">${s.called} of ${s.races} races called; includes seats not up${s.reported_count && s.seats_secured_incl_reported ? `; with ${s.reported_count} reported in news: D ${s.seats_secured_incl_reported.D} · R ${s.seats_secured_incl_reported.R}` : ""}</div></div>
        <div class="card${flagged.length ? " flag-card" : ""}"><div class="label">Rated safe, now competitive</div><div class="big">${flagged.length}</div><div class="note">Seats rated Solid (or unrated) before the election with a count now within 5 points</div></div>
      </div>
      ${flagged.length ? `<h2><span class="flag-badge">Rated safe</span> Seats rated safe, now competitive (${flagged.length})</h2>
      <div class="table-scroll"><table class="ratings"><thead><tr><th>Race</th><th>Pre-election</th><th>Count so far</th><th>Status</th></tr></thead>
        <tbody>${flagged.map(k => { const r = races[k]; const w = r.winner || {}; return `<tr class="flagged"><td>${esc(r.name)}${r.flagged_since ? `<div class="meta">since ${fmtDate(r.flagged_since.slice(0, 10))}, ${r.flagged_since.endsWith("pm") ? "evening" : "morning"}</div>` : ""}</td><td>${r.final_rating ? pill(r.final_rating, "sm") : '<span class="meta">unrated</span>'}</td><td>${resultLine(r)}<div class="meta">margin ${r.margin_pts} pts</div></td><td>${r.called ? `Called: ${partyPill(w.party || "?")} ${esc(w.name || "")}` : "Not called"}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="meta">Margins come from the Wikipedia results table and can be partial while counting continues.</p>` : ""}
      <h2>Competitive races not yet called (${unc.length})</h2>
      <div class="table-scroll"><table class="ratings"><thead><tr><th>Race</th><th>Final rating</th><th>Count so far</th><th>Latest</th></tr></thead>
        <tbody>${unc.map(k => { const r = races[k]; return `<tr${r.rated_safe_now_close ? ' class="flagged"' : ""}><td>${esc(r.name)}${flagBadge(r)}</td><td>${r.final_rating ? pill(r.final_rating, "sm") : '<span class="meta">unrated</span>'}</td><td>${resultLine(r)}</td><td>${firstLink(k)}</td></tr>`; }).join("") || '<tr><td colspan="4" class="meta">None. Every competitive race has been called.</td></tr>'}</tbody></table></div>
      <h2>Called since the last update (${newly.length} competitive)</h2>
      <div class="card"><ul class="list">${newly.map(k => { const r = races[k]; const w = r.winner || {}; return `<li${r.rated_safe_now_close ? ' class="flagged"' : ""}><div class="item-title">${esc(r.name)} ${partyPill(w.party || "?")} ${esc(w.name || "")}${flagBadge(r)}</div><div class="item-meta">Final rating ${esc(r.final_rating || "unrated")} · ${esc(r.status_text)}</div></li>`; }).join("") || '<li class="meta">No competitive race was called since the last update.</li>'}</ul></div>
      <div class="meta" style="margin:10px 0">${(R.pending_safe || []).length} safe seats are not yet marked in the results table; that is usually table lag, not a close race (none has a count within 5 points).${(R.pending_safe || []).length ? ` <details><summary>Show them</summary>${R.pending_safe.map(esc).join(", ")}</details>` : ""} Sources: <a href="${esc(R.sources.house)}" target="_blank" rel="noopener">House results</a>, <a href="${esc(R.sources.senate)}" target="_blank" rel="noopener">Senate results</a>.</div>
      ${SCORE ? `<h2>Scorecard: how the forecasts did (${esc(SCORE.status)})</h2>
        <div class="card"><p class="meta">${esc(SCORE.caveat)} ${SCORE.competitive_called_pct}% of races rated Lean D to Lean R are called.</p>
        <div class="table-scroll" style="box-shadow:none;margin:10px 0"><table class="ratings"><thead><tr><th>Forecaster</th><th>Right</th><th>Wrong</th><th>Tossups</th><th>Accuracy</th></tr></thead>
        <tbody>${SCORE.forecasters.map(f => `<tr><td>${esc(f.forecaster)}</td><td>${f.right}</td><td>${f.wrong}</td><td>${f.tossups}</td><td>${f.accuracy_pct == null ? "n/a" : f.accuracy_pct + "%"}</td></tr>`).join("")}</tbody></table></div>
        ${SCORE.polls ? `<p>Polls missed by ${SCORE.polls.mean_abs_error} points on average across ${SCORE.polls.races} tracked races (signed error ${SCORE.polls.mean_signed_error > 0 ? "+" : ""}${SCORE.polls.mean_signed_error}; positive means Republicans were underestimated).</p>` : ""}</div>` : ""}
      ${(R.problems || []).length ? `<h2>Data problems</h2><div class="card"><ul class="list">${R.problems.map(p => `<li class="meta">${esc(p)}</li>`).join("")}</ul></div>` : ""}`;
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
    try { const r = await fetch(dataUrl("briefs/index.json"), { cache: "no-store" }); if (r.ok) idx = await r.json(); } catch (e) { /* no archive yet */ }
    if (date) {
      try { const r = await fetch(dataUrl(`briefs/${date}.md`), { cache: "no-store" }); const txt = r.ok ? await r.text() : "Brief not found."; $app.innerHTML = `<a class="back" href="#briefs">← All briefs</a><div class="brief">${md(txt)}</div>`; } catch (e) { $app.innerHTML = `<div class="error">Could not load that brief.</div>`; }
      return;
    }
    $app.innerHTML = `<h1>Past briefs</h1>${idx.dates.length ? `<div class="card"><ul class="list">${idx.dates.map(d => `<li><a href="#briefs/${d}" class="item-title">${fmtDate(d.slice(0, 10))}${d.endsWith("-pm") ? " (evening)" : ""}</a></li>`).join("")}</ul></div>` : '<div class="callout">No briefs archived yet.</div>'}`;
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
