# Deployment Guide (plain-language walkthrough)

This explains how to get the calendar live on the internet, using two free
accounts. No coding required from you - just account creation, a few clicks,
and copy/pasting a couple of values.

## What we're using and why

- **GitHub** - a free place to store the project's code and to run the daily
  "check PokerAtlas for updates" job on a timer. Think of it as the robot's
  home and its alarm clock.
- **Cloudflare Workers** - a free hosting service that publishes the webpage,
  serves the live calendar feed, and (via its Browser Rendering feature)
  fetches PokerAtlas pages on the scraper's behalf so PokerAtlas doesn't
  block the request. It watches your GitHub project and automatically
  re-publishes whenever something changes (including after the daily
  scrape).

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
   (`scraper/`, `public/`, `worker/`, `.github/`, `wrangler.jsonc`,
   `README.md`, `DEPLOYMENT_GUIDE.md`, `.gitignore`) - GitHub's upload box
   accepts whole folders when you drag them from your computer's file
   browser. (If a nested folder like `.github/workflows/` doesn't come
   through intact, use "Add file → Create new file" and type the full path
   into the filename box instead - more reliable for nested files.)
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
2. In the
