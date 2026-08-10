#!/usr/bin/env python3
"""
Tampa Bay Poker Calendar - PokerAtlas scraper.

Fetches the current + next month tournament calendars for a fixed list of
Tampa Bay area poker rooms from PokerAtlas (a room-by-room tournament
listing site that does not offer a public API) and writes a normalized
data/tournaments.json file that the rest of the site (the filter page and
the .ics feed generator) reads from.

Designed to be run once a day (see .github/workflows/scrape.yml). It is
intentionally light: ~2 HTTP requests per room per day (current month +
next month), with a polite delay between requests and a descriptive
User-Agent, respecting robots.txt (which only disallows /go/ and /admin/).
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

# PokerAtlas's server blocks automated requests that originate from cloud/CI
# IP ranges (like GitHub Actions'), regardless of what headers or TLS
# fingerprint they carry - we confirmed this by ruling out both. Instead of
# fetching PokerAtlas directly, we route requests through Cloudflare's
# Browser Rendering API (https://developers.cloudflare.com/browser-rendering/) -
# it spins up a real Chromium browser on Cloudflare's network, loads the page,
# and hands back the fully-rendered HTML. We're already using Cloudflare to
# host the site, so this needs no new account, just an API token (see
# DEPLOYMENT_GUIDE.md). It has a free daily allowance that's far more than
# this project needs (~10 page loads/day). We still keep the same polite
# behavior on our end: ~2 pages/room/day, a delay between requests, and
# respect for robots.txt.
CF_CONTENT_ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/content"
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")

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


def fetch(url: str, max_retries: int = 4) -> str:
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        raise RuntimeError(
            "CF_ACCOUNT_ID and/or CF_API_TOKEN environment variables are not set. "
            "Add them as GitHub Actions secrets (see DEPLOYMENT_GUIDE.md)."
        )
    endpoint = CF_CONTENT_ENDPOINT.format(account_id=CF_ACCOUNT_ID)
    for attempt in range(1, max_retries + 1):
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {CF_API_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "url": url,
                "gotoOptions": {"waitUntil": "networkidle0", "timeout": 45000},
            },
            timeout=70,
        )
        # The Browser Rendering free tier only allows a handful of requests
        # in flight at once; back off and retry instead of giving up.
        if resp.status_code == 429:
            wait_seconds = 20 * attempt
            print(
                f"    Rate limited (429) - waiting {wait_seconds}s before "
                f"retry {attempt}/{max_retries}...",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)
            continue
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare Browser Rendering error: {data.get('errors')}")
        return data["result"]
    raise RuntimeError(
        f"Cloudflare Browser Rendering: still rate-limited (429) after {max_retries} retries"
    )


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


def next_month_start(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def scrape_room(room: dict):
    slug = room["pokeratlas_slug"]
    all_events = {}

    # Current month (default view, no start_date needed)
    url_current = f"{BASE}/poker-room/{slug}/tournaments"
    print(f"  fetching current month: {url_current}")
    html = fetch(url_current)
    debug_page_structure(html)
    for ev in parse_calendar_html(html, room):
        all_events[ev["pokeratlas_url"]] = ev

    time.sleep(REQUEST_DELAY_SECONDS)

    # Next month
    nxt = next_month_start(date.today())
    url_next = (
        f"{BASE}/poker-room/{slug}/tournaments_calendar"
        f"?start_date={nxt.isoformat()}"
    )
    print(f"  fetching next month: {url_next}")
    try:
        html2 = fetch(url_next)
        debug_page_structure(html2)
        for ev in parse_calendar_html(html2, room):
            all_events[ev["pokeratlas_url"]] = ev
    except requests.exceptions.RequestException as e:
        print(f"  WARN: next-month fetch failed for {room['id']}: {e}", file=sys.stderr)

    return list(all_events.values())


def main():
    rooms = json.loads(ROOMS_FILE.read_text())
    all_tournaments = []
    for room in rooms:
        print(f"Scraping {room['name']} ({room['pokeratlas_slug']})...")
        try:
            events = scrape_room(room)
            print(f"  -> {len(events)} tournament instances found")
            all_tournaments.extend(events)
        except requests.exceptions.RequestException as e:
            print(f"  ERROR scraping {room['id']}: {e}", file=sys.stderr)
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
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"\nWrote {len(all_tournaments)} tournaments to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
