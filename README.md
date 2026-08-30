# YC / Speedrun Launch Radar ⚡

A persistent monitor that watches **four sources** for new Y Combinator and
a16z Speedrun companies — and catches founders announcing their acceptance
on X/Twitter and LinkedIn **before** the official directories list them.

Built for GTM teams: get Slack alerts the moment a new company appears, or
the moment a founder posts "we got into YC".

## What it monitors

| # | Source | Type | Cost |
|---|--------|------|------|
| 1 | [YC Directory](https://www.ycombinator.com/companies) (via its public Algolia index) | Confirmed listings | free |
| 2 | [a16z Speedrun Directory](https://speedrun.a16z.com/companies) (public API) | Confirmed listings | free |
| 3 | X / Twitter (via Apify) | ⚡ early founder signals | ~$0.40 / 1,000 tweets |
| 4 | LinkedIn (via Apify) | ⚡ early founder signals | ~$0.005 / post |

**Typical cost: under $10/month** running at the spec cadence (8h), and can
be tuned down to ~$0–5/mo inside Apify's free monthly credit.

## Two kinds of alerts

**🆕 New listing** — a company just appeared in the YC or Speedrun
directory (diffed against everything ever seen; never re-alerts):

> **Evergrove** just launched on the YC directory (Summer 2026)
> — one-liner description — link

**⚡ Early signal** — a founder announced YC/Speedrun acceptance on
X or LinkedIn *and* their company is not yet in either directory:

> ⚡ **Kai Yang** (@ChihYang04): "Got into YC S26, but deferred…"
> Founder post on X — not yet listed in any directory — link to post

The classifier filters out replies, congratulations, rejections,
"how do I get into YC" threads, and already-listed companies — only
genuine founder announcements reach your channel.

## Setup (about 10 minutes, 2 copy-paste tokens)

You need **two tokens total**. Both directories are public and need nothing.

### 1. Slack app (~3 min, no code)

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
   → **From an app manifest**.
2. Pick your workspace, paste the contents of
   [`slack_app_manifest.json`](slack_app_manifest.json), click through.
3. Install the app to your workspace (**Install to Workspace** button).
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
5. Create a channel (e.g. `#yc-radar`) and **invite the bot**:
   in the channel type `/invite @Launch Radar`.

### 2. Apify token (~2 min, covers BOTH social sources)

1. Create a free account at [apify.com](https://apify.com).
2. Go to Settings → API & Integrations → copy your **API token**.

### 3. Configure

Copy `.env.example` to `.env` and fill in the two tokens:

```
SLACK_BOT_TOKEN=xoxb-...      # from step 1
APIFY_TOKEN=apify_api_...     # from step 2
SLACK_CHANNEL=#yc-radar        # any channel the bot is in
```

That's it. The YC directory key is **auto-extracted at runtime** (the bot
reads it from the live YC page, so it never goes stale), and the a16z
Speedrun API is public — zero configuration for both directories.

### 4. Run

**With Docker (recommended):**

```bash
docker compose up -d     # starts the monitor + Pond server
docker compose logs -f   # watch the first sweep happen
```

**Without Docker (Python 3.10+):**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py poll        # persistent loop (sweeps every 8h)
```

**Deploy one-click** to Railway / Render / Fly: point the service at this
repo, set the env vars from `.env`, done. The DB is a single SQLite file in
`./data` — mount a small volume to keep state across deploys.

### 5. Verify it works

```bash
python manage.py doctor      # checks ALL 4 sources live, green/red per source
python manage.py test-alert  # posts a sample alert to Slack in ~10 seconds
python manage.py status      # counts of everything tracked so far
```

If `doctor` is green on all four lines and you can see the test alert in
Slack, you're done. The first real sweep posts the 3 newest YC listings so
you immediately see what production alerts look like (the rest of the
existing directory is marked as known, so no alert flood).

## Persistent monitoring (not a one-shot script)

- `manage.py poll` runs **forever**: sweeps all four sources, sleeps 8h
  (configurable via `POLL_INTERVAL_SECONDS`), sweeps again.
- All state lives in SQLite (`data/radar.db`): every company and every
  social post ever seen. Restarts, redeploys, and crashes never re-alert.
- Each source is timestamped (`manage.py status` shows last-poll times).
- LinkedIn polling defaults to once per 24h for cost control
  (`LINKEDIN_POLL_INTERVAL_SECONDS`) — bump it to 8h for faster signals.

## Pond integration (optional, on by default)

The repo implements Pond's agent protocol
([docs.joinpond.ai](https://docs.joinpond.ai)):

- `GET /manifest` — public agent manifest
- `POST /runs` — Pond-triggered on-demand sweep or status report
  (Bearer auth via `POND_ACCESS_KEY`, protocol version header)

To register: create an agent at
[joinpond.ai/agent/create](https://joinpond.ai/agent/create), point it at
your deployment URL, set `POND_ACCESS_KEY` in your env. Pond can then
health-check and trigger the radar remotely. Disable with
`POND_ENABLED=false` — the monitor runs identically without it.

`python manage.py serve` runs the Pond endpoints (port 8000);
`docker compose up` runs monitor + Pond server together.

## How it works (60-second tour)

```
radar/
  config.py            all knobs are env vars (.env)
  models.py            Company + SocialPost shapes
  db.py                SQLite state: companies, signals, alerts, meta
  sources/
    yc_directory.py    YC's public Algolia index (key auto-extracted,
                       full 6,000+ company coverage via batch facets)
    speedrun.py        a16z Speedrun public API (all cohorts)
    x_twitter.py       Apify tweet search → SocialPost
    linkedin.py        Apify LinkedIn post search → SocialPost
  classifier.py        deterministic rules: announcement? founder?
                       already listed? → early | listed | noise
  slack.py             alert formatting + delivery
  poller.py            the loop: diff directories, classify posts, alert
  pond.py              Pond protocol server (FastAPI)
manage.py              doctor | poll | test-alert | status | serve
```

Adding another social platform later = writing one adapter file that
yields `SocialPost` objects; the classifier, dedup, and alerting are
source-agnostic.

## Costs, honestly

| Item | Cost |
|------|------|
| YC directory + Speedrun | $0 (public) |
| X via Apify (`apidojo/tweet-scraper`) | $0.40 / 1,000 tweets |
| LinkedIn via Apify (`apimaestro/linkedin-posts-search-scraper-no-cookies`) | $0.005 / post |
| **Typical monthly** (8h X / 24h LinkedIn) | **~$2–10/mo** |

Staying inside Apify's **free $5/mo credit** is easy: keep LinkedIn at
once a day and X at every 8h.

## Honest limitations

- The X/LinkedIn layers use maintained third-party scrapers on Apify —
  no official APIs, no OAuth, nothing to apply for. If a platform changes
  its markup, the actor usually gets patched within days; `manage.py
  doctor` will show red if a source breaks.
- The classifier is deliberately conservative (rules, not an LLM): it
  favors precision over recall so your channel stays high-signal. Tune
  `radar/classifier.py` patterns freely.
- Search-based social monitoring sees public posts matching the search
  terms — it's not a guarantee of catching every single announcement.
