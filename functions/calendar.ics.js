// Cloudflare Pages Function: GET /calendar.ics
//
// Generates a live, filterable .ics calendar feed from the JSON data the
// scraper (scraper/scraper.py) produces once a day. Because this is served
// from a fixed URL and calendar apps (Google/Apple/Outlook) periodically
// re-fetch subscribed .ics URLs on their own schedule, the same subscribe
// link keeps itself up to date as the data changes - no re-subscribing
// needed.
//
// Query params (all optional, all comma-separated where multiple):
//   room     - one or more room ids from scraper/rooms.json (e.g. derby-lane)
//   game     - one or more game codes (NLH, PLO, PLO8, O8, MIXED, FLH, STUD, OTHER)
//   minbuyin - minimum buy-in in dollars
//   maxbuyin - maximum buy-in in dollars
//   name     - a friendly name to put in the calendar title (optional)
//
// Example: /calendar.ics?room=derby-lane,silks-tampa-bay-downs&game=NLH&maxbuyin=100

const DEFAULT_DURATION_HOURS = 4;

function icsEscape(text) {
  return String(text)
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\n/g, "\\n");
}

// Fold lines longer than 75 octets per RFC 5545 (simple char-based fold,
// good enough for our ASCII-ish content).
function foldLine(line) {
  if (line.length <= 75) return line;
  let out = "";
  let rest = line;
  let first = true;
  while (rest.length > 0) {
    const chunkLen = first ? 75 : 74;
    out += (first ? "" : "\r\n ") + rest.slice(0, chunkLen);
    rest = rest.slice(chunkLen);
    first = false;
  }
  return out;
}

function dtLocal(dateStr, timeStr) {
  // dateStr: "2026-08-01", timeStr: "6:30PM" -> "20260801T183000"
  const m = timeStr.match(/(\d{1,2}):(\d{2})(AM|PM)/i);
  if (!m) return null;
  let [, hh, mm, ap] = m;
  hh = parseInt(hh, 10);
  mm = parseInt(mm, 10);
  if (ap.toUpperCase() === "PM" && hh !== 12) hh += 12;
  if (ap.toUpperCase() === "AM" && hh === 12) hh = 0;
  const [y, mo, d] = dateStr.split("-");
  const pad = (n) => String(n).padStart(2, "0");
  return `${y}${mo}${d}T${pad(hh)}${pad(mm)}00`;
}

function addHours(dtLocalStr, hours) {
  const y = parseInt(dtLocalStr.slice(0, 4), 10);
  const mo = parseInt(dtLocalStr.slice(4, 6), 10) - 1;
  const d = parseInt(dtLocalStr.slice(6, 8), 10);
  const hh = parseInt(dtLocalStr.slice(9, 11), 10);
  const mm = parseInt(dtLocalStr.slice(11, 13), 10);
  // Use a UTC-based Date purely as a day/hour arithmetic helper (no tz math).
  const dt = new Date(Date.UTC(y, mo, d, hh, mm));
  dt.setUTCHours(dt.getUTCHours() + hours);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${dt.getUTCFullYear()}${pad(dt.getUTCMonth() + 1)}${pad(dt.getUTCDate())}` +
    `T${pad(dt.getUTCHours())}${pad(dt.getUTCMinutes())}00`
  );
}

// Minimal but standards-compliant VTIMEZONE block for US Eastern time, so
// calendar clients that don't already know America/New_York still render
// events at the correct local time.
const VTIMEZONE = `BEGIN:VTIMEZONE
TZID:America/New_York
X-LIC-LOCATION:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE`;

export async function onRequestGet(context) {
  const { request } = context;
  const url = new URL(request.url);
  const params = url.searchParams;

  const roomFilter = (params.get("room") || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const gameFilter = (params.get("game") || "")
    .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  const minBuyin = params.has("minbuyin") ? Number(params.get("minbuyin")) : null;
  const maxBuyin = params.has("maxbuyin") ? Number(params.get("maxbuyin")) : null;
  const feedName = params.get("name") || "Tampa Bay Poker Tournaments";

  const dataUrl = new URL("/data/tournaments.json", url.origin);
  const dataResp = await fetch(dataUrl.toString(), { cf: { cacheTtl: 300 } });
  if (!dataResp.ok) {
    return new Response("Tournament data unavailable", { status: 502 });
  }
  const data = await dataResp.json();

  let tournaments = data.tournaments || [];
  if (roomFilter.length) {
    tournaments = tournaments.filter((t) => roomFilter.includes(t.room_id));
  }
  if (gameFilter.length) {
    tournaments = tournaments.filter((t) => gameFilter.includes(t.game_code));
  }
  if (minBuyin !== null && !Number.isNaN(minBuyin)) {
    tournaments = tournaments.filter((t) => t.buyin >= minBuyin);
  }
  if (maxBuyin !== null && !Number.isNaN(maxBuyin)) {
    tournaments = tournaments.filter((t) => t.buyin <= maxBuyin);
  }

  const lines = [];
  lines.push("BEGIN:VCALENDAR");
  lines.push("VERSION:2.0");
  lines.push("PRODID:-//Tampa Bay Poker//Tournament Calendar//EN");
  lines.push("CALSCALE:GREGORIAN");
  lines.push("METHOD:PUBLISH");
  lines.push(`X-WR-CALNAME:${icsEscape(feedName)}`);
  lines.push("X-WR-TIMEZONE:America/New_York");
  // Ask calendar clients to re-poll roughly every 6 hours (data updates daily).
  lines.push("REFRESH-INTERVAL;VALUE=DURATION:PT6H");
  lines.push("X-PUBLISHED-TTL:PT6H");
  lines.push(VTIMEZONE);

  const dtstamp =
    new Date().toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";

  for (const t of tournaments) {
    const dtStartLocal = dtLocal(t.date, t.time);
    if (!dtStartLocal) continue;
    const dtEndLocal = addHours(dtStartLocal, DEFAULT_DURATION_HOURS);
    const uidSource = t.pokeratlas_url || `${t.room_id}-${t.date}-${t.time}-${t.name}`;
    const uid = `${uidSource.replace(/[^a-zA-Z0-9]/g, "").slice(-40)}@tampabaypoker.com`;

    const descParts = [
      `Buy-in: $${t.buyin}`,
      `Game: ${t.game_type}`,
      `More info: ${t.pokeratlas_url}`,
    ];
    if (t.structure_url) descParts.push(`Room site / structure: ${t.structure_url}`);
    // Escape each part individually, THEN join with the literal ICS
    // line-break escape "\n" - escaping the whole joined string would
    // double-escape that intentional "\n" marker.
    const description = descParts.map(icsEscape).join("\\n");

    lines.push("BEGIN:VEVENT");
    lines.push(`UID:${uid}`);
    lines.push(`DTSTAMP:${dtstamp}`);
    lines.push(`DTSTART;TZID=America/New_York:${dtStartLocal}`);
    lines.push(`DTEND;TZID=America/New_York:${dtEndLocal}`);
    lines.push(foldLine(`SUMMARY:${icsEscape(`${t.room_name} - ${t.name}`)}`));
    lines.push(foldLine(`DESCRIPTION:${description}`));
    lines.push(foldLine(`LOCATION:${icsEscape(`${t.room_name}, ${t.city}, FL`)}`));
    if (t.pokeratlas_url) lines.push(`URL:${t.pokeratlas_url}`);
    lines.push("END:VEVENT");
  }

  lines.push("END:VCALENDAR");
  const body = lines.join("\r\n") + "\r\n";

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/calendar; charset=utf-8",
      "Content-Disposition": 'inline; filename="tampa-bay-poker.ics"',
      "Cache-Control": "public, max-age=1800",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
