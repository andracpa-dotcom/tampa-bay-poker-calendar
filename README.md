# Tampa Bay Poker Tournament Calendar

A live, filterable poker tournament calendar for Tampa Bay area poker rooms,
scraped daily from [PokerAtlas](https://www.pokeratlas.com) (which has no
public API), packaged as:

- a subscribable `.ics` calendar feed people can filter by room / game type / buy-in
- a filter webpage where visitors pick what they want and get a personalized subscribe link
- an embeddable version of that page for dropping into another website (e.g. via iframe)

**Start here:** [`DEPLOYMENT_GUIDE.md`](./DEPLOYMENT_GUIDE.md) walks through everything
needed to get this live, written for a non-developer.

## How it's built

| Piece | What it does | Where |
|---|---|---|
| Scraper | Fetches each room's PokerAtlas tournament calendar once a day, parses it, writes `public/data/tournaments.json` | `scraper/scraper.py`, runs via `.github/workflows/scrape.yml` (GitHub Actions, free) |
| Calendar feed | Turns that JSON into a filtered `.ics` feed on request | `functions/calendar.ics.js` (Cloudflare Pages Function) |
| Filter page | Checkboxes for room / game / buy-in, builds a subscribe link, shows a live table | `public/index.html`, `public/app.js`, `public/style.css` |
| Hosting | Serves the static page + the live feed | Cloudflare Pages (free) |

Rooms covered (edit `scraper/rooms.json` to add/remove):

- Win Derby at Derby Lane (St. Petersburg)
- Seminole Hard Rock Tampa
- The Silks at Tampa Bay Downs (Tampa)
- TGT Poker and Racebook (Tampa)
- One Eyed Jacks (Sarasota)

## Local data note

`public/data/tournaments.json` currently ships with a small set of **sample
tournaments** (clearly marked `"sample_data": true`) so the site works the
moment it's deployed. The first time the daily GitHub Action runs, it
overwrites this file with real, live-scraped data.
