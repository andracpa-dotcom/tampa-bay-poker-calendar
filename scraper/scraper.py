#!/usr/bin/env python3
"""
Tampa Bay Poker Calendar - PokerAtlas scraper.

Fetches the current-month tournament calendar for a fixed list of Tampa Bay
area poker rooms from PokerAtlas (a room-by-room tournament listing site
that does not offer a public API) and writes a normalized
data/tournaments.json file that the rest of the site (the filter page and
the .ics feed generator) reads from.

We only fetch the main "/tournaments" page per room (not a separate
"next month" page) - PokerAtlas's next-month URL turns out to be an
AJAX-only fragment that returns an empty shell when loaded as a normal
page, bot-blocking aside. In practice this isn't a big loss: PokerAtlas's
calendar grid always shows full weeks, so the last row of the current
month's table already spills a few days into next month.

Designed to be run once a day (see .github/workflows/scrape.yml). It is
intentionally light: 1 HTTP request per room per day, with a polite delay
between requests, respecting robots.txt (which only disallows /go/ and
/admin/).
"""
import json
import os
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.pokeratlas.com"

# PokerAtlas sits behind Cloudflare's bot protection, which blocks requests
# from IP ranges it recognizes as automation infrastructure - we confirmed
# this by ruling out request headers and TLS/browser fingerprint (still
# blocked even via a real headless Chromium browser through Cloudflare's own
# Browser Rendering service - its IPs are apparently recognized too). A
# request from a normal, non-flagged IP gets the real page with no trouble.
# So we route through ScrapeOps (https://scrapeops.io), a proxy aggregator
# that rotates through many different IPs specifically to avoid this kind of
# block. Requires a free ScrapeOps account and its key set as the
# SCRAPEOPS_API_KEY GitHub Actions secret (see DEPLOYMENT_GUIDE.md). Plain
# requests cost 1 credit each against ScrapeOps' free 1,000-credit/month
# allowance (we use ~300/month); if a plain request still comes back looking
# blocked, we automatically retry once with Cloudflare-bypass mode enabled
# (10 credits) rather than failing outright.
SCRAPEOPS_ENDPOINT = "https://proxy.scrapeops.io/v1/"
SCRAPEOPS_API_KEY = os.environ.get("SCRAPEOPS_API_KEY")

REQUEST_DELAY_SECONDS = 8

ROOT = Path(__file__).resolve().parent.parent
ROOMS_FILE = Path(__file__).resolve().parent / "rooms.json"
OUTPUT_FILE = ROOT / "public" / "data" / "tournaments.json"

# --- game type classification ------------------------------------------------
# Matched against the tournament's URL slug, in order (first match wins).
GAME_PATTERNS = [
    (r"nl-ha\b", "NLH / PLO Mix", "MIXED"),
    (r"pl-omaha-?8|plo-?8|pot-limit-omaha-8", "PL Omaha Hi-Lo", "PLO8"),
    (r"pl-omaha", "PL Omaha", "PLO"),
    (r"fl-omaha-?8|limit-omaha-8|omaha-8-or-better", "Limit Omaha Hi-Lo", "O8"),
    (r"o8-s8|omaha-8-stud-8", "Mixed (Omaha/Stud Hi-Lo)", "MIXED"),
    (r"\bnl-holdem", "NL Hold'em", "NLH"),
    (r"\bfl-holdem", "Limit Hold'em", "FLH"),
    (r"\bm-poker-tournament|-mixed-", "Mixed Games", "MIXED"),
    (r"stud", "Stud", "STUD"),
]

DATE_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})")
TIME_BUYIN_RE = re.compile(
    r"(?P<time>\d{1,2}:\d{2}\s*[ap]m)\s*\$?(?P<buyin>[\d,]*)\s*(?P<code>[A-Za-z0-9/]*)",
    re.IGNORECASE,
)


def classify_game(slug: str):
    for pattern, label, code in GAME_PATTERNS:
        if re.search(pattern, slug, re.IGNORECASE):
            return label, code
    return "Other", "OTHER"


def prettify_name(slug: str, game_label: str, buyin: int) -> str:
    """Best-effort human-readable event name derived from the PokerAtlas
    URL slug, e.g. '...-60-1100am-nl-holdem-60-1-2k-gte-poker-tournament-<id>'
    becomes '$60 NL Hold'em - 1.2K GTD'.
    This is a heuristic, not a perfect parser - PokerAtlas doesn't expose
    tournament names in the calendar grid without an extra page fetch per
    tournament, which we intentionally avoid to keep scraping light.
    """
    # Strip trailing tournament id / 'poker-tournament' / room+time+buyin prefix
    s = slug
    s = re.sub(r"-poker-tournament(-[a-z0-9-]+)?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^.*?\d{1,2}(am|pm)-", "", s)  # drop room name + time
    s = re.sub(r"^(nl|pl|fl)-holdem-?|^pl-omaha-?8?-?|^o8-s8-?|^fl-omaha-?8-?", "", s)
    s = re.sub(r"^\d+-", "", s)  # stray leading buy-in repeat
    words = [w for w in s.split("-") if w]
    label = " ".join(words).strip()
    label = label.title() if label else ""
    # Fix acronyms/abbreviations AFTER title-casing (title() would otherwise
    # turn "GTD" into "Gtd").
    label = re.sub(r"\bGte\b", "GTD", label)
    label = re.sub(r"\bKo\b", "K.O.", label)
    label = re.sub(r"\bNlh\b", "NLH", label)
    label = re.sub(r"\bPlo\b", "PLO", label)
    # Reconnect "1 2K" -> "1.2K" (dashes in the original name meant a decimal)
    label = re.sub(r"\b(\d+)\s(\d+K)\b", r"\1.\2", label)
    prefix = f"${buyin} {game_label}" if buyin else f"{game_label} Freeroll"
    if label:
        return f"{prefix} - {label}"
    return prefix


def parse_calendar_html(html: str, room: dict):
    """Parse a PokerAtlas room tournament-calendar page into a list of
    tournament dicts. Structure-agnostic: for each table cell that contains
    both a date string (e.g. 'Saturday Aug 01, 2026') and one or more links
    to /poker-tournament/, extract the date once and every tournament link
    within that cell.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Every calendar day is rendered as a <td>; find all of them and inspect
    # the ones that actually contain tournament links.
    cells = soup.find_all("td")
    for cell in cells:
        links = [
            a for a in cell.find_all("a", href=True)
            if "/poker-tournament/" in a["href"]
        ]
        if not links:
            continue

        cell_text = cell.get_text(" ", strip=True)
        date_match = DATE_RE.search(cell_text)
        if not date_match:
            continue
        month_str, day_str, year_str = date_match.groups()
        try:
            event_date = datetime.strptime(
                f"{month_str} {day_str} {year_str}", "%b %d %Y"
            ).date()
        except ValueError:
            try:
                event_date = datetime.strptime(
                    f"{month_str} {day_str} {year_str}", "%B %d %Y"
                ).date()
            except ValueError:
                continue

        for a in links:
            link_text = a.get_text(" ", strip=True)
            href = urljoin(BASE, a["href"])
            m = TIME_BUYIN_RE.search(link_text)
            if not m:
                continue
            time_str = m.group("time").upper().replace(" ", "")
            buyin_str = (m.group("buyin") or "0").replace(",", "")
            buyin = int(buyin_str) if buyin_str.isdigit() else 0

            slug = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            game_label, game_code = classify_game(slug)
            name = prettify_name(slug, game_label, buyin)

            results.append({
                "room_id": room["id"],
                "room_name": room["name"],
                "city": room["city"],
                "date": event_date.isoformat(),
                "time": time_str,
                "name": name,
                "game_type": game_label,
                "game_code": game_code,
                "buyin": buyin,
                "pokeratlas_url": href,
                "structure_url": room.get("website"),
            })
    return results


BLOCKED_PAGE_MARKERS = (
    "attention required",
    "just a moment",
    "checking your browser",
    "cf-error",
    "access denied",
)


def looks_blocked(html: str) -> bool:
    """Heuristic check for a Cloudflare/anti-bot interstitial instead of the
    real PokerAtlas page (these run ~5-6K characters and contain none of our
    expected tournament links, versus tens of thousands of characters for a
    real calendar page)."""
    if "/poker-tournament/" in html:
        return False
    lowered = html.lower()
    return len(html) < 15000 or any(marker in lowered for marker in BLOCKED_PAGE_MARKERS)


def _scrapeops_request(url: str, extra_params: dict) -> requests.Response:
    params = {"api_key": SCRAPEOPS_API_KEY, "url": url}
    params.update(extra_params)
    return requests.get(SCRAPEOPS_ENDPOINT, params=params, timeout=90)


def fetch(url: str, max_retries: int = 3) -> str:
    if not SCRAPEOPS_API_KEY:
        raise RuntimeError(
            "SCRAPEOPS_API_KEY environment variable is not set. "
            "Add it as a GitHub Actions secret (see DEPLOYMENT_GUIDE.md)."
        )

    # First try: a plain request (1 API credit).
    for attempt in range(1, max_retries + 1):
        resp = _scrapeops_request(url, {})
        if resp.status_code == 429 or resp.status_code >= 500:
            # 429 = rate limited; 5xx = ScrapeOps couldn't reach the target
            # this time (their docs say this is common and transient, and
            # doesn't consume a credit) - back off and try again.
            wait_seconds = 15 * attempt
            print(
                f"    Got HTTP {resp.status_code} - waiting {wait_seconds}s "
                f"before retry {attempt}/{max_retries}...",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)
            continue
        resp.raise_for_status()
        html = resp.text
        if not looks_blocked(html):
            return html
        print(
            "    Plain request came back looking blocked - retrying with "
            "Cloudflare-bypass mode enabled (costs more credits)...",
            file=sys.stderr,
        )
        break
    else:
        raise RuntimeError(f"ScrapeOps: request kept failing after {max_retries} retries")

    # Fallback: Cloudflare-bypass mode (10 credits) if the plain request was blocked.
    for attempt in range(1, max_retries + 1):
        resp = _scrapeops_request(url, {"bypass": "cloudflare_level_1"})
        if resp.status_code == 429 or resp.status_code >= 500:
            wait_seconds = 15 * attempt
            print(
                f"    Got HTTP {resp.status_code} - waiting {wait_seconds}s "
                f"before retry {attempt}/{max_retries}...",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)
            continue
        resp.raise_for_status()
        return resp.text

    raise RuntimeError(f"ScrapeOps: request kept failing after {max_retries} retries")


def debug_page_structure(html: str) -> None:
    """Temporary diagnostic: prints clues about the page's real HTML shape
    so we can see exactly why parse_calendar_html found 0 tournaments,
    without having to guess. Safe to leave in - it's cheap and only prints
    a handful of lines per room.
    """
    soup = BeautifulSoup(html, "html.parser")
    print(f"    DEBUG: fetched {len(html)} chars")
    title = soup.find("title")
    print(f"    DEBUG: <title> = {title.get_text(strip=True) if title else '(none)'}")
    links = soup.find_all("a", href=lambda h: h and "/poker-tournament/" in h)
    print(f"    DEBUG: found {len(links)} <a href=.../poker-tournament/...> links anywhere on the page")
    tds = soup.find_all("td")
    print(f"    DEBUG: found {len(tds)} <td> elements total")
    tds_with_links = [td for td in tds if td.find("a", href=lambda h: h and "/poker-tournament/" in h)]
    print(f"    DEBUG: {len(tds_with_links)} of those <td> elements directly contain a tournament link")
    if links:
        sample = links[0]
        print(f"    DEBUG: sample link text = {sample.get_text(' ', strip=True)!r}")
        print(f"    DEBUG: sample link href = {sample['href']!r}")
        ancestor_tags = [p.name for p in sample.parents if p.name]
        print(f"    DEBUG: sample link's ancestor tags (innermost first) = {ancestor_tags[:8]}")
    else:
        print("    DEBUG: no tournament links found anywhere in the fetched HTML at all")
        snippet = re.sub(r"\s+", " ", html).strip()[:800]
        print(f"    DEBUG: first 800 chars of raw HTML: {snippet!r}")
    if tds_with_links:
        sample_cell_text = tds_with_links[0].get_text(" ", strip=True)[:200]
        print(f"    DEBUG: sample <td>-with-link text = {sample_cell_text!r}")


def scrape_room(room: dict):
    slug = room["pokeratlas_slug"]
    all_events = {}

    # Current month (default view, no start_date needed). The calendar grid
    # always shows full weeks, so this naturally includes a few trailing
    # days from next month too - see the module docstring for why we don't
    # fetch a separate "next month" page.
    url_current = f"{BASE}/poker-room/{slug}/tournaments"
    print(f"  fetching: {url_current}")
    html = fetch(url_current)
    debug_page_structure(html)
    for ev in parse_calendar_html(html, room):
        all_events[ev["pokeratlas_url"]] = ev

    return list(all_events.values())


def load_previous_tournaments() -> dict:
    """Room id -> list of tournament dicts, loaded from whatever's already
    live on disk. Used as a fallback for rooms that fail to scrape this run,
    so a bad day for one room doesn't wipe out its otherwise-good data -
    we just keep showing yesterday's listings for that room until it
    scrapes successfully again."""
    if not OUTPUT_FILE.exists():
        return {}
    try:
        data = json.loads(OUTPUT_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    by_room: dict = {}
    for t in data.get("tournaments", []):
        by_room.setdefault(t["room_id"], []).append(t)
    return by_room


def main():
    rooms = json.loads(ROOMS_FILE.read_text())
    previous_by_room = load_previous_tournaments()
    all_tournaments = []
    stale_rooms = []
    for room in rooms:
        print(f"Scraping {room['name']} ({room['pokeratlas_slug']})...")
        try:
            events = scrape_room(room)
            print(f"  -> {len(events)} tournament instances found")
            all_tournaments.extend(events)
        except Exception as e:
            # Don't let one room's persistent failure (rate limits, a
            # transient ScrapeOps outage, etc.) take down the whole run -
            # log it, fall back to that room's last-known-good data if we
            # have any, and move on to the next room.
            print(f"  ERROR scraping {room['id']}: {e}", file=sys.stderr)
            fallback = previous_by_room.get(room["id"], [])
            if fallback:
                print(
                    f"  -> keeping {len(fallback)} previously-scraped "
                    f"tournaments for this room instead of dropping it",
                    file=sys.stderr,
                )
                all_tournaments.extend(fallback)
                stale_rooms.append(room["id"])
        time.sleep(REQUEST_DELAY_SECONDS)

    all_tournaments.sort(key=lambda t: (t["date"], t["time"], t["room_name"]))

    # Only keep today and future (drop anything that scraped as past-dated,
    # which can happen right at a month boundary).
    today_iso = date.today().isoformat()
    all_tournaments = [t for t in all_tournaments if t["date"] >= today_iso]

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "rooms": rooms,
        "tournament_count": len(all_tournaments),
        "tournaments": all_tournaments,
        # Room ids whose data in this file is carried over from a previous
        # run because today's scrape failed for them - not shown in the UI,
        # just here for troubleshooting.
        "stale_rooms": stale_rooms,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {len(all_tournaments)} tournaments to {OUTPUT_FILE}")
    if stale_rooms:
        print(f"(Rooms using carried-over data this run: {', '.join(stale_rooms)})")


if __name__ == "__main__":
    main()
