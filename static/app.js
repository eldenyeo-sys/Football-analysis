const REFRESH_INTERVAL_MS = 20 * 1000;

const matchesEl = document.getElementById("matches");
const lastUpdatedEl = document.getElementById("last-updated");
const refreshBtn = document.getElementById("refresh-btn");
const countdownEl = document.getElementById("countdown");
const confidenceFilterEl = document.getElementById("confidence-filter");

let secondsUntilRefresh = REFRESH_INTERVAL_MS / 1000;
let lastMatches = [];
let previousOdds = {}; // match_id -> {home, draw, away}, used to flash changed odds

function tickCountdown() {
  secondsUntilRefresh -= 1;
  if (secondsUntilRefresh <= 0) {
    secondsUntilRefresh = REFRESH_INTERVAL_MS / 1000;
  }
  const m = Math.floor(secondsUntilRefresh / 60);
  const s = String(secondsUntilRefresh % 60).padStart(2, "0");
  countdownEl.textContent = `Auto-refresh in ${m}:${s}`;
}

function fmtKickoff(iso, isLive) {
  if (isLive) return "LIVE";
  if (!iso) return "Time TBC";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function oddsChip(label, value, direction) {
  const dirClass = direction ? ` odds-changed odds-${direction}` : "";
  const arrow = direction === "up" ? " &#9650;" : direction === "down" ? " &#9660;" : "";
  return `<div class="odds-chip${dirClass}"><span class="label">${label}</span>${value ?? "-"}${arrow}</div>`;
}

function computeOddsChanges(matchId, odds) {
  const prev = previousOdds[matchId];
  const changes = { home: null, draw: null, away: null };
  if (prev) {
    for (const key of ["home", "draw", "away"]) {
      if (typeof odds[key] === "number" && typeof prev[key] === "number" && odds[key] !== prev[key]) {
        changes[key] = odds[key] > prev[key] ? "up" : "down";
      }
    }
  }
  previousOdds[matchId] = { ...odds };
  return changes;
}

function probBar(label, value) {
  return `
    <div class="prob-bar-row">
      <span class="prob-bar-label">${label}</span>
      <div class="prob-bar-track"><div class="prob-bar-fill" style="width:${value}%"></div></div>
      <span class="prob-bar-value">${value}%</span>
    </div>`;
}

function formChips(form, teamName) {
  if (!form.matches || form.matches.length === 0) {
    return '<span class="no-data">No recent results found</span>';
  }
  const sourceTag = form.source === "espn" ? ' <span class="source-tag">via ESPN</span>' : "";
  const chips = form.matches
    .map(
      (m) => `<span
        class="chip chip-${m.result}"
        title="${m.opponent}: ${m.score}"
        data-team="${escapeAttr(teamName)}"
        data-opponent="${escapeAttr(m.opponent)}"
        data-goals-for="${m.goals_for}"
        data-goals-against="${m.goals_against}"
        data-date="${escapeAttr(m.date || "Unknown date")}"
        data-league="${escapeAttr(m.league)}"
        data-result="${m.result}"
      >${m.result}</span>`
    )
    .join("");
  return `<span class="chips">${chips}</span> <span class="no-data">${form.record}</span>${sourceTag}`;
}

function topProbability(probabilities) {
  return Math.max(probabilities.home, probabilities.draw, probabilities.away);
}

function h2hList(h2h) {
  if (!h2h || h2h.length === 0) {
    return '<p class="no-data">No meeting found in the currently available data window.</p>';
  }
  const items = h2h
    .map((m) => `<li>${m.date} &middot; ${m.home_team} ${m.score} ${m.away_team} (${m.league})</li>`)
    .join("");
  return `<ul class="h2h-list">${items}</ul>`;
}

function liveScoreBadge(match) {
  if (!match.is_live) return "";
  if (!match.live_score) {
    return '<div class="live-score live-score-unknown">Live &mdash; score unavailable</div>';
  }
  const ls = match.live_score;
  return `
    <div class="live-score">
      <span class="live-score-value">${match.home_team} ${ls.home} - ${ls.away} ${match.away_team}</span>
      <span class="live-score-status">${ls.status || ""}</span>
    </div>`;
}

function sofascoreSearchUrl(homeTeam, awayTeam) {
  // We don't scrape Sofascore (their ToS bans automated access), so this links
  // to a search rather than a specific match page -- we have no way to know
  // their internal match ID without scraping. site:-scoped Google search
  // reliably lands on the right match page regardless of Sofascore's own URL
  // scheme, which we don't otherwise have a reason to track.
  const query = `site:sofascore.com ${homeTeam} vs ${awayTeam}`;
  return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

function matchCard(match) {
  const p = match.prediction;
  return `
    <div class="card${match.is_live ? " is-live" : ""}" data-confidence="${p.confidence}" data-match-id="${match.match_id}">
      <div class="card-header">
        <span>${match.league}</span>
        <span class="${match.is_live ? "live-badge" : ""}">${fmtKickoff(match.kickoff, match.is_live)}</span>
      </div>
      <div class="teams">
        ${match.home_team} vs ${match.away_team}
        <a class="sofascore-link" href="${sofascoreSearchUrl(match.home_team, match.away_team)}" target="_blank" rel="noopener" title="Find this match on Sofascore for more stats">Sofascore &#8599;</a>
      </div>
      ${match.is_live ? '<div class="watch-live-hint">Tap to watch live &rarr;</div>' : ""}
      ${liveScoreBadge(match)}

      <div class="odds-row">
        ${oddsChip("1 (Home)", match.odds.home, match.oddsChange?.home)}
        ${oddsChip("X (Draw)", match.odds.draw, match.oddsChange?.draw)}
        ${oddsChip("2 (Away)", match.odds.away, match.oddsChange?.away)}
      </div>

      <div class="prediction">
        <strong>${p.outcome}</strong>
        <span class="confidence-badge confidence-${p.confidence}">${p.confidence} confidence (${topProbability(p.probabilities)}%)${p.live_adjusted ? ' <span class="live-tag">&#9679; live</span>' : ""}</span>
      </div>
      <div class="prob-bars">
        ${probBar("Home", p.probabilities.home)}
        ${probBar("Draw", p.probabilities.draw)}
        ${probBar("Away", p.probabilities.away)}
      </div>

      <div class="section-title">Recent Form</div>
      <div class="form-row"><span>${match.home_team}</span>${formChips(match.home_form, match.home_team)}</div>
      <div class="form-row"><span>${match.away_team}</span>${formChips(match.away_form, match.away_team)}</div>

      <div class="section-title">Head-to-Head</div>
      ${h2hList(match.head_to_head)}
      <button class="h2h-view-more" data-home="${escapeAttr(match.home_team)}" data-away="${escapeAttr(match.away_team)}" data-league="${escapeAttr(match.league)}">View more &rarr;</button>

      <button class="ai-analysis-btn" data-match-id="${match.match_id}">&#10024; AI Analysis</button>
    </div>`;
}

function escapeAttr(value) {
  return String(value).replace(/"/g, "&quot;");
}

function render() {
  closeFormPopover(); // cards are about to be rebuilt, so any open popover would go stale
  const filter = confidenceFilterEl.value;
  const filtered =
    filter === "all" ? lastMatches : lastMatches.filter((m) => m.prediction.confidence === filter);

  if (filtered.length === 0) {
    matchesEl.innerHTML = `<div class="error-banner">No matches match the "${filter}" confidence filter right now.</div>`;
    return;
  }
  matchesEl.innerHTML = filtered.map(matchCard).join("");
}

async function loadMatches() {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Loading...";
  try {
    const res = await fetch("/api/matches");
    const data = await res.json();

    if (!res.ok) {
      matchesEl.innerHTML = `<div class="error-banner">Couldn't load data: ${data.error || res.statusText}</div>`;
      return;
    }

    if (!data.matches || data.matches.length === 0) {
      matchesEl.innerHTML = `<div class="error-banner">No upcoming matches found right now.</div>`;
      return;
    }

    for (const match of data.matches) {
      match.oddsChange = computeOddsChanges(match.match_id, match.odds);
    }
    lastMatches = data.matches;
    render();
    lastUpdatedEl.textContent = `Last updated ${new Date(data.generated_at).toLocaleTimeString()}`;
    secondsUntilRefresh = REFRESH_INTERVAL_MS / 1000;
  } catch (err) {
    matchesEl.innerHTML = `<div class="error-banner">Couldn't reach the server: ${err.message}</div>`;
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Refresh";
  }
}

refreshBtn.addEventListener("click", () => loadMatches());
confidenceFilterEl.addEventListener("change", () => render());

matchesEl.innerHTML = '<div class="loading">Loading matches...</div>';
loadMatches();
setInterval(loadMatches, REFRESH_INTERVAL_MS);
setInterval(tickCountdown, 1000);
tickCountdown();

// --- Live match-tracker modal ---
const LIVE_POLL_INTERVAL_MS = 5 * 1000;

const modalBackdrop = document.getElementById("live-modal-backdrop");
const modalClose = document.getElementById("live-modal-close");
const modalLeague = document.getElementById("live-modal-league");
const modalTitle = document.getElementById("live-modal-title");
const modalScore = document.getElementById("live-modal-score");
const modalStatus = document.getElementById("live-modal-status");
const modalNote = document.getElementById("live-modal-note");
const modalVenue = document.getElementById("live-modal-venue");
const modalEvents = document.getElementById("live-modal-events");
const modalUpdated = document.getElementById("live-modal-updated");

let liveModalMatch = null;
let liveModalTimer = null;

const EVENT_ICONS = {
  Goal: "⚽",
  "Own Goal": "⚽🔄",
  "Penalty - Scored": "⚽",
  "Yellow Card": "🟨",
  "Red Card": "🟥",
  Substitution: "🔁",
};

function renderVenue(venue) {
  if (!venue || !venue.name) {
    modalVenue.textContent = "";
    return;
  }
  const place = [venue.city, venue.country].filter(Boolean).join(", ");
  modalVenue.textContent = place ? `${venue.name} — ${place}` : venue.name;
}

function renderEvents(events, homeTeam, awayTeam) {
  if (!events || events.length === 0) {
    modalEvents.innerHTML = "";
    return;
  }
  const rows = events
    .map((e) => {
      const icon = EVENT_ICONS[e.type] || "•";
      const teamName = e.side === "home" ? homeTeam : e.side === "away" ? awayTeam : "";
      const sideClass = e.side === "away" ? " event-side-away" : "";
      const who = e.player ? `${e.player}${teamName ? ` (${teamName})` : ""}` : e.type;
      return `
        <div class="event-row${sideClass}">
          <span class="event-minute">${e.minute || ""}</span>
          <span class="event-icon">${icon}</span>
          <span class="event-detail">${e.type}${e.player ? ` — ${who}` : ""}</span>
        </div>`;
    })
    .join("");
  modalEvents.innerHTML = `<div class="modal-events-title">Match Events</div>${rows}`;
}

function renderLiveModal(liveScore) {
  if (!liveScore) {
    modalScore.textContent = "–";
    modalStatus.textContent = "";
    modalNote.textContent = "Score unavailable right now — the source that mirrors live scores for this match hasn't picked it up. Still polling.";
    modalVenue.textContent = "";
    modalEvents.innerHTML = "";
    return;
  }
  modalScore.textContent = `${liveScore.home} – ${liveScore.away}`;
  modalStatus.textContent = liveScore.status || "";
  modalNote.textContent = liveScore.is_final
    ? "Match has finished."
    : "";
  renderVenue(liveScore.venue);
  renderEvents(liveScore.events, liveModalMatch.home_team, liveModalMatch.away_team);
}

async function pollLiveModal() {
  if (!liveModalMatch) return;
  const params = new URLSearchParams({
    home: liveModalMatch.home_team,
    away: liveModalMatch.away_team,
    league: liveModalMatch.league,
    kickoff: liveModalMatch.kickoff || "",
  });
  try {
    const res = await fetch(`/api/live-score?${params.toString()}`);
    const data = await res.json();
    if (res.ok) {
      renderLiveModal(data.live_score);
      modalUpdated.textContent = `updated ${new Date(data.generated_at).toLocaleTimeString()}`;
    }
  } catch (err) {
    modalNote.textContent = `Couldn't reach the server: ${err.message}`;
  }
}

function openLiveModal(match) {
  liveModalMatch = match;
  modalLeague.textContent = match.league;
  modalTitle.textContent = `${match.home_team} vs ${match.away_team}`;
  renderLiveModal(match.live_score);
  modalUpdated.textContent = "";
  modalBackdrop.classList.remove("hidden");

  pollLiveModal();
  liveModalTimer = setInterval(pollLiveModal, LIVE_POLL_INTERVAL_MS);
}

function closeLiveModal() {
  modalBackdrop.classList.add("hidden");
  liveModalMatch = null;
  if (liveModalTimer) {
    clearInterval(liveModalTimer);
    liveModalTimer = null;
  }
}

matchesEl.addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (chip) {
    toggleFormPopover(chip);
    return;
  }
  const h2hBtn = event.target.closest(".h2h-view-more");
  if (h2hBtn) {
    openH2HModal(h2hBtn.dataset.home, h2hBtn.dataset.away, h2hBtn.dataset.league);
    return;
  }
  const aiBtn = event.target.closest(".ai-analysis-btn");
  if (aiBtn) {
    const match = lastMatches.find((m) => String(m.match_id) === aiBtn.dataset.matchId);
    if (match) openAIModal(match);
    return;
  }
  const card = event.target.closest(".card.is-live");
  if (!card) return;
  const match = lastMatches.find((m) => String(m.match_id) === card.dataset.matchId);
  if (match) openLiveModal(match);
});

modalClose.addEventListener("click", closeLiveModal);
modalBackdrop.addEventListener("click", (event) => {
  if (event.target === modalBackdrop) closeLiveModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeLiveModal();
    closeH2HModal();
    closeFormPopover();
    closeAIModal();
  }
});

// --- AI Analysis modal ---
const aiModalBackdrop = document.getElementById("ai-modal-backdrop");
const aiModalClose = document.getElementById("ai-modal-close");
const aiModalTitle = document.getElementById("ai-modal-title");
const aiModalBody = document.getElementById("ai-modal-body");

function buildAIContext(match) {
  return {
    home_team: match.home_team,
    away_team: match.away_team,
    league: match.league,
    odds: match.odds,
    prediction: match.prediction,
    home_form: {
      record: match.home_form.record,
      ppg: match.home_form.ppg,
      gd_pg: match.home_form.gd_pg,
    },
    away_form: {
      record: match.away_form.record,
      ppg: match.away_form.ppg,
      gd_pg: match.away_form.gd_pg,
    },
    head_to_head: (match.head_to_head || [])
      .slice(0, 5)
      .map((m) => `${m.date}: ${m.home_team} ${m.score} ${m.away_team} (${m.league})`),
  };
}

async function openAIModal(match) {
  aiModalTitle.textContent = `${match.home_team} vs ${match.away_team}`;
  aiModalBody.innerHTML = '<p class="no-data">Generating analysis&hellip;</p>';
  aiModalBackdrop.classList.remove("hidden");

  try {
    const res = await fetch("/api/ai-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildAIContext(match)),
    });
    const data = await res.json();
    if (!res.ok) {
      aiModalBody.innerHTML = `<p class="no-data">Couldn't generate analysis: ${data.error || res.statusText}</p>`;
      return;
    }
    aiModalBody.textContent = data.analysis;
  } catch (err) {
    aiModalBody.innerHTML = `<p class="no-data">Couldn't reach the server: ${err.message}</p>`;
  }
}

function closeAIModal() {
  aiModalBackdrop.classList.add("hidden");
}

aiModalClose.addEventListener("click", closeAIModal);
aiModalBackdrop.addEventListener("click", (event) => {
  if (event.target === aiModalBackdrop) closeAIModal();
});

// --- Recent-form chip click -> match score preview popover ---
const RESULT_LABELS = { W: "Win", D: "Draw", L: "Loss" };
let formPopoverEl = null;
let formPopoverForChip = null;

function closeFormPopover() {
  if (formPopoverEl) {
    formPopoverEl.remove();
    formPopoverEl = null;
    formPopoverForChip = null;
  }
}

function toggleFormPopover(chip) {
  const reopeningSameChip = formPopoverForChip === chip;
  closeFormPopover();
  if (reopeningSameChip) return;

  const { team, opponent, goalsFor, goalsAgainst, date, league, result } = chip.dataset;
  const popover = document.createElement("div");
  popover.className = "form-popover";
  popover.innerHTML = `
    <div class="form-popover-result form-popover-${result}">${RESULT_LABELS[result] || result}</div>
    <div class="form-popover-score">${team} ${goalsFor} &ndash; ${goalsAgainst} ${opponent}</div>
    <div class="form-popover-meta">${date} &middot; ${league}</div>`;
  document.body.appendChild(popover);

  const rect = chip.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - popRect.width / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - popRect.width - 8));
  let top = rect.bottom + 8;
  if (top + popRect.height > window.innerHeight - 8) {
    top = rect.top - popRect.height - 8;
  }
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;

  formPopoverEl = popover;
  formPopoverForChip = chip;
}

document.addEventListener("click", (event) => {
  if (!formPopoverEl) return;
  if (event.target.closest(".chip") || event.target.closest(".form-popover")) return;
  closeFormPopover();
});

// --- Head-to-head "view more" modal ---
const h2hModalBackdrop = document.getElementById("h2h-modal-backdrop");
const h2hModalClose = document.getElementById("h2h-modal-close");
const h2hModalTitle = document.getElementById("h2h-modal-title");
const h2hModalSummary = document.getElementById("h2h-modal-summary");
const h2hModalList = document.getElementById("h2h-modal-list");

function renderH2HSummary(summary, homeTeam, awayTeam) {
  if (!summary || summary.meetings_count === 0) {
    h2hModalSummary.innerHTML = '<p class="no-data">No past meetings found in the currently available data.</p>';
    return;
  }
  h2hModalSummary.innerHTML = `
    <div class="h2h-summary">
      <div class="h2h-summary-stat"><span class="value">${summary.team_a_wins}</span><span class="label">${homeTeam} wins</span></div>
      <div class="h2h-summary-stat"><span class="value">${summary.draws}</span><span class="label">Draws</span></div>
      <div class="h2h-summary-stat"><span class="value">${summary.team_b_wins}</span><span class="label">${awayTeam} wins</span></div>
    </div>
    <div class="h2h-summary-extra">
      ${summary.meetings_count} meeting${summary.meetings_count === 1 ? "" : "s"} &middot;
      goals ${summary.team_a_goals}&ndash;${summary.team_b_goals} &middot;
      avg ${summary.avg_goals_per_game} goals/game
    </div>`;
}

function renderH2HList(meetings) {
  if (!meetings || meetings.length === 0) {
    h2hModalList.innerHTML = "";
    return;
  }
  const items = meetings
    .map(
      (m) => `
        <li>
          <span>${m.home_team} ${m.score} ${m.away_team}</span>
          <span class="h2h-date">${m.date} &middot; ${m.league}</span>
        </li>`
    )
    .join("");
  h2hModalList.innerHTML = `<ul>${items}</ul>`;
}

async function openH2HModal(home, away, league) {
  h2hModalTitle.textContent = `${home} vs ${away}`;
  h2hModalSummary.innerHTML = '<p class="no-data">Loading&hellip;</p>';
  h2hModalList.innerHTML = "";
  h2hModalBackdrop.classList.remove("hidden");

  const params = new URLSearchParams({ home, away, league });
  try {
    const res = await fetch(`/api/head-to-head?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      h2hModalSummary.innerHTML = `<p class="no-data">Couldn't load head-to-head: ${data.error || res.statusText}</p>`;
      return;
    }
    renderH2HSummary(data.summary, home, away);
    renderH2HList(data.meetings);
  } catch (err) {
    h2hModalSummary.innerHTML = `<p class="no-data">Couldn't reach the server: ${err.message}</p>`;
  }
}

function closeH2HModal() {
  h2hModalBackdrop.classList.add("hidden");
}

h2hModalClose.addEventListener("click", closeH2HModal);
h2hModalBackdrop.addEventListener("click", (event) => {
  if (event.target === h2hModalBackdrop) closeH2HModal();
});
