# Deployment Guide (plain-language walkthrough)

This explains how to get the calendar live on the internet, using two free
accounts. No coding required from you - just account creation, a few clicks,
and copy/pasting a couple of values. Budget about 30-45 minutes.

## What we're using and why

- **GitHub** - a free place to store the project's code and to run the daily
  "check PokerAtlas for updates" job on a timer. Think of it as the robot's
  home and its alarm clock.
- **Cloudflare Pages** - a free hosting service that publishes the webpage
  and serves the live calendar feed. It watches your GitHub project and
  automatically re-publishes whenever something changes (including after
  the daily scrape).

Both are free for a project this size - there is no ongoing cost.

---

## Part 1 - Create a GitHub account and upload the project

1. Go to [github.com](https://github.com) and click **Sign up**. Use any
   email address (your Yahoo address is fine). Choose a username - this
   guide will call it `YOUR-USERNAME`.
2. Once signed in, click the **+** in the top right → **New repository**.
   - Repository name: `tampa-bay-poker-calendar` (or anything you like)
   - Keep it **Public** (required for the free tier of everything below;
     nothing sensitive lives in this project - it's just tournament times)
   - Don't check any of the "initialize with" boxes
   - Click **Create repository**
3. On the next page, look for **"uploading an existing file"** (a link in
   the instructions GitHub shows you). Click it.
4. From the project folder you were given, drag in every file and folder
   (`scraper/`, `public/`, `functions/`, `.github/`, `README.md`,
   `DEPLOYMENT_GUIDE.md`, `.gitignore`) - GitHub's upload box accepts whole
   folders when you drag them from your computer's file browser.
5. Scroll down, add a commit message like "Initial upload", and click
   **Commit changes**.

You now have the whole project on GitHub. You can always come back to this
repository page to see the code or make small edits (like adding a room)
directly in the browser using GitHub's built-in editor (click any file, then
the pencil icon).

---

## Part 2 - Create a Cloudflare account and connect it to GitHub

1. Go to [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up)
   and create a free account.
2. In the Cloudflare dashboard, find **Workers & Pages** in the left sidebar
   → click **Create** → **Pages** tab → **Connect to Git**.
3. Authorize Cloudflare to access your GitHub account, then select the
   `tampa-bay-poker-calendar` repository.
4. On the build settings screen, set:
   - **Framework preset:** None
   - **Build command:** (leave blank)
   - **Build output directory:** `public`
5. Click **Save and Deploy**. Cloudflare will publish the site - after a
   minute you'll get a live URL like
   `https://tampa-bay-poker-calendar.pages.dev`.
6. Open that URL. You should see the filter page with a small set of sample
   tournaments already listed (clearly marked as sample data until the real
   scraper runs - see Part 3).
7. Test the feed directly by visiting
   `https://tampa-bay-poker-calendar.pages.dev/calendar.ics` in your
   browser - it should download or display a block of calendar text
   starting with `BEGIN:VCALENDAR`. That confirms the live feed works.

From now on, **any time new code is pushed to GitHub, Cloudflare
automatically re-publishes the site** - including every day after the
scraper commits fresh tournament data. You don't need to do anything for
that to keep happening.

(Optional, later: in Cloudflare Pages → your project → **Custom domains**,
you can point a subdomain of your own site at this, e.g.
`calendar.tampabaypoker.com`, instead of the `.pages.dev` address. Not
required to get started.)

---

## Part 3 - Turn on the daily scraper

The scraper code and its daily schedule are already in the project
(`.github/workflows/scrape.yml`), but GitHub Actions schedules only "wake up"
after you've confirmed the workflow at least once.

1. On your GitHub repository page, click the **Actions** tab.
2. You should see a workflow called **"Daily PokerAtlas scrape."** Click it.
3. Click **Run workflow** (a button on the right) → **Run workflow** again
   to confirm. This runs it immediately instead of waiting for tomorrow.
4. After about a minute, refresh the page - you'll see a run with a green
   checkmark (success) or a red X (something went wrong; click into it to
   read the log, or send it to me and I'll help debug it).
5. On success, check your repository's `public/data/tournaments.json` file
   (click it in the file list) - it should now show `"sample_data"` gone
   and real tournament entries with today's `generated_at` timestamp.
6. Within a minute or two, Cloudflare will notice the new commit and
   re-publish automatically. Refresh your `.pages.dev` site - you're now
   showing live PokerAtlas data.

From here on, it runs itself once a day (currently set for 6am Eastern -
edit the `cron` line in `.github/workflows/scrape.yml` to change the time;
the two numbers are `minute hour` in UTC).

---

## Part 4 - Embed it on your WordPress site

On the WordPress page where you want the calendar to appear, add a **Custom
HTML block** (in the block editor, type `/html` and choose "Custom HTML"),
and paste this in, replacing the URL with your actual Cloudflare Pages URL
(or custom domain if you set one up):

```html
<iframe
  src="https://tampa-bay-poker-calendar.pages.dev/?embed=1"
  style="width: 100%; border: 0;"
  height="1100"
  loading="lazy"
  title="Tampa Bay Poker Tournament Calendar">
</iframe>
```

Notes:

- The `?embed=1` at the end tells the page to hide its own header/footer so
  it fits cleanly inside your site's page instead of looking like a separate
  site.
- `height="1100"` is a fixed starting guess. If the list of tournaments runs
  long and gets a scrollbar inside the box, increase that number; there's no
  automatic resizing in this version.
- If your WordPress theme or a security plugin blocks iframes, look for a
  setting like "allow custom HTML" or ask your host to allow iframes from
  `pages.dev` domains.

If you'd rather not use an iframe at all, a simpler (but less visually
integrated) option is to just link to the page directly:
`https://tampa-bay-poker-calendar.pages.dev/` - e.g. a "Tournament Calendar"
button/menu item on your site that opens it.

---

## How the filtering and subscribing works (for your visitors)

1. They visit the page, check the rooms / game types they care about, and
   optionally set a buy-in range.
2. The page builds a personalized link behind two buttons: **Subscribe**
   (opens directly in the Calendar app on an iPhone/Mac) and **Add to
   Google Calendar**. There's also a plain link they can copy into Outlook
   or Android calendar apps via "subscribe from URL."
3. This is a **live subscription**, not a one-time file download - once
   someone subscribes, their calendar app automatically checks the link
   every so often (typically every few hours, controlled by their calendar
   app, not by us) and picks up new/changed tournaments without them doing
   anything again.

---

## Structure sheets

PokerAtlas's own tournament pages generally don't include a structure sheet
PDF - just buy-in, time, and format. So each calendar entry links to:

- the PokerAtlas listing for that tournament, and
- the poker room's own website (e.g. `derbylanepoker.com`,
  `tgtpoker.com`), where structure sheets are more likely to be posted.

If a specific room publishes structure sheets at a predictable URL, tell me
the pattern and I can wire up a direct link per room in
`scraper/rooms.json` (there's already a `website` field there for this).

---

## Costs

At this scale (5 rooms, ~2 requests per room per day, a small static
website), everything here stays within:

- GitHub: free for public repositories, including Actions minutes at this
  volume.
- Cloudflare Pages: free tier easily covers this (generous request and
  bandwidth limits for a site like this).

There is no credit card required for either signup at this scale.

---

## Maintenance & troubleshooting

- **Check scrape health:** GitHub repo → **Actions** tab → look for green
  checkmarks once a day. Click a run to see how many tournaments were found
  per room.
- **PokerAtlas changes its page layout:** scraping tools like this can break
  if a site redesigns its pages. If a room suddenly shows 0 tournaments in
  the Action log, that's the likely cause - let me know and I'll update
  `scraper/scraper.py`.
- **Add or remove a room:** edit `scraper/rooms.json` (room id, display
  name, its PokerAtlas URL slug, its own website, city) directly in GitHub's
  file editor, commit, and the next daily run (or a manual "Run workflow")
  will pick it up.
- **Change how far ahead it looks:** currently the scraper pulls the current
  month plus next month from PokerAtlas. That's in `scraper/scraper.py` if
  you ever want more/less lookahead.
