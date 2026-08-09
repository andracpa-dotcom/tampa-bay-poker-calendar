// Tampa Bay Poker Calendar - filter page logic.
// Loads /data/tournaments.json (refreshed daily by the scraper), lets the
// visitor filter by room / game type / buy-in, shows a live table, and
// builds personalized subscribe links that point at /calendar.ics with the
// matching query string. Because /calendar.ics regenerates itself from the
// latest data on every request, the SAME subscribe link keeps working as
// tournaments change - visitors only need to subscribe once.

const GAME_LABELS = {
  NLH: "No-Limit Hold'em",
  PLO: "Pot-Limit Omaha",
  PLO8: "PL Omaha Hi-Lo",
  O8: "Limit Omaha Hi-Lo",
  FLH: "Limit Hold'em",
  MIXED: "Mixed Games",
  STUD: "Stud",
  OTHER: "Other",
};

const state = {
  rooms: [],
  tournaments: [],
  selectedRooms: new Set(),
  selectedGames: new Set(),
  minBuyin: "",
  maxBuyin: "",
};

const els = {};

function $(sel) { return document.querySelector(sel); }

function buildQueryString() {
  const params = new URLSearchParams();
  if (state.selectedRooms.size && state.selectedRooms.size < state.rooms.length) {
    params.set("room", Array.from(state.selectedRooms).join(","));
  }
  const allGameCodes = Object.keys(GAME_LABELS).filter((c) =>
    state.tournaments.some((t) => t.game_code === c)
  );
  if (state.selectedGames.size && state.selectedGames.size < allGameCodes.length) {
    params.set("game", Array.from(state.selectedGames).join(","));
  }
  if (state.minBuyin !== "") params.set("minbuyin", state.minBuyin);
  if (state.maxBuyin !== "") params.set("maxbuyin", state.maxBuyin);
  return params.toString();
}

function icsUrl(protocol) {
  const qs = buildQueryString();
  const base = `${window.location.origin}/calendar.ics`;
  const url = qs ? `${base}?${qs}` : base;
  if (protocol === "webcal") {
    return url.replace(/^https?:\/\//, "webcal://");
  }
  return url;
}

function googleCalendarUrl() {
  const httpsUrl = icsUrl("https");
  return `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(httpsUrl)}`;
}

function passesFilters(t) {
  if (state.selectedRooms.size && !state.selectedRooms.has(t.room_id)) return false;
  if (state.selectedGames.size && !state.selectedGames.has(t.game_code)) return false;
  if (state.minBuyin !== "" && t.buyin < Number(state.minBuyin)) return false;
  if (state.maxBuyin !== "" && t.buyin > Number(state.maxBuyin)) return false;
  return true;
}

function formatDateHeading(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return dt.toLocaleDateString(undefined, {
    weekday: "long", month: "short", day: "numeric",
  });
}

function renderFilters() {
  const roomBox = $("#room-filters");
  roomBox.innerHTML = state.rooms.map((r) => `
    <label class="checkbox-row">
      <input type="checkbox" data-kind="room" value="${r.id}" ${state.selectedRooms.has(r.id) ? "checked" : ""}>
      ${r.name}
    </label>
  `).join("");

  const gameCodesPresent = Array.from(new Set(state.tournaments.map((t) => t.game_code)))
    .sort((a, b) => (GAME_LABELS[a] || a).localeCompare(GAME_LABELS[b] || b));
  const gameBox = $("#game-filters");
  gameBox.innerHTML = gameCodesPresent.map((code) => `
    <label class="checkbox-row">
      <input type="checkbox" data-kind="game" value="${code}" ${state.selectedGames.has(code) ? "checked" : ""}>
      ${GAME_LABELS[code] || code}
    </label>
  `).join("");

  document.querySelectorAll('input[data-kind="room"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      cb.checked ? state.selectedRooms.add(cb.value) : state.selectedRooms.delete(cb.value);
      renderAll();
    });
  });
  document.querySelectorAll('input[data-kind="game"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      cb.checked ? state.selectedGames.add(cb.value) : state.selectedGames.delete(cb.value);
      renderAll();
    });
  });
}

function renderTable() {
  const filtered = state.tournaments.filter(passesFilters);
  const tbody = $("#tournament-body");
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="empty-state">No tournaments match these filters right now. Try widening your buy-in range or selecting more rooms/games.</div></td></tr>`;
    $("#result-count").textContent = "0 tournaments";
    return;
  }

  let lastDate = null;
  const rows = [];
  for (const t of filtered) {
    if (t.date !== lastDate) {
      rows.push(`<tr class="date-group"><td colspan="5">${formatDateHeading(t.date)}</td></tr>`);
      lastDate = t.date;
    }
    rows.push(`
      <tr>
        <td>${t.time}</td>
        <td><span class="badge">${t.room_name}</span></td>
        <td>${t.name}</td>
        <td class="buyin-cell">${t.buyin ? "$" + t.buyin : "Free"}</td>
        <td class="links-cell">
          <a href="${t.pokeratlas_url}" target="_blank" rel="noopener">PokerAtlas</a>
          ${t.structure_url ? `<span class="sep">|</span><a href="${t.structure_url}" target="_blank" rel="noopener">Room site</a>` : ""}
        </td>
      </tr>
    `);
  }
  tbody.innerHTML = rows.join("");
  $("#result-count").textContent = `${filtered.length} tournament${filtered.length === 1 ? "" : "s"}`;
}

function renderSubscribeLinks() {
  $("#subscribe-webcal").href = icsUrl("webcal");
  $("#subscribe-google").href = googleCalendarUrl();
  $("#subscribe-https").value = icsUrl("https");
}

function renderAll() {
  renderTable();
  renderSubscribeLinks();
}

async function init() {
  if (new URLSearchParams(window.location.search).get("embed") === "1") {
    document.body.classList.add("embed");
  }

  const resp = await fetch("/data/tournaments.json", { cache: "no-store" });
  const data = await resp.json();
  state.rooms = data.rooms || [];
  state.tournaments = data.tournaments || [];

  $("#updated-note").textContent = data.generated_at
    ? `Data last updated ${new Date(data.generated_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      })}`
    : "";

  renderFilters();
  renderAll();

  $("#minbuyin").addEventListener("input", (e) => { state.minBuyin = e.target.value; renderAll(); });
  $("#maxbuyin").addEventListener("input", (e) => { state.maxBuyin = e.target.value; renderAll(); });

  $("#copy-btn").addEventListener("click", async () => {
    const input = $("#subscribe-https");
    input.select();
    try {
      await navigator.clipboard.writeText(input.value);
    } catch (e) {
      document.execCommand("copy");
    }
    const feedback = $("#copy-feedback");
    feedback.classList.add("show");
    setTimeout(() => feedback.classList.remove("show"), 1800);
  });
}

document.addEventListener("DOMContentLoaded", init);
