"""
Poller — the persistent monitoring loop.

One `cycle()` does a full sweep of all four sources and fires only
incremental alerts (SQLite remembers everything ever seen):

  1. YC directory + Speedrun directory → diff against DB → alert new listings
  2. X + LinkedIn keyword searches → classifier → alert ⚡ early signals
  3. Stamp last-poll timestamps.

`run_forever()` wraps cycles on the configured interval (8h default).
Cold start: the very first run marks all current companies as known
(so you don't get 4,000 alerts), but posts the few newest listings so
a fresh install immediately demonstrates what alerts look like.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone

import httpx

from . import classifier, db, slack
from .config import Settings
from .models import Company, SocialPost
from .sources import linkedin, speedrun, x_twitter, yc_directory


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ------------------------------------------------------------------ dirs ---

def poll_directory(
    client: httpx.Client,
    settings: Settings,
    conn,
    send,
    source_name: str,
    fetch,
) -> int:
    """Fetch one directory, diff against state, alert on new companies."""
    # Cold-start flag is PER SOURCE: the very first poll of each directory
    # marks everything currently listed as known (no 4,000-alert flood) and
    # showcases a few of the newest listings.
    is_cold = db.get_meta(conn, f"cold_start_done:{source_name}") != "1"
    companies: list[Company] = fetch()

    known = db.known_slugs(conn, source_name)
    fresh = [c for c in companies if c.slug not in known]
    log(f"{source_name}: fetched {len(companies)}, {len(fresh)} new")

    # Showcase the NEWEST listings (launched_at desc when the source has it).
    if fresh:
        fresh = sorted(
            fresh,
            key=lambda c: (c.raw or {}).get("launched_at") or 0,
            reverse=True,
        )

    alerted = 0
    for c in companies:
        is_new = db.upsert_company(conn, c)
        if not is_new:
            continue
        if is_cold:
            continue  # record silently; showcase happens below
        send("new_company", c)
        alerted += 1

    if is_cold and fresh:
        for c in fresh[: settings.cold_start_alerts]:
            send("new_company", c)
            alerted += 1

    if is_cold:
        db.set_meta(conn, f"cold_start_done:{source_name}", "1")
    db.set_meta(conn, f"last_poll:{source_name}", db.utcnow())
    return alerted


# ---------------------------------------------------------------- social ---

def poll_social(
    client: httpx.Client,
    settings: Settings,
    conn,
    send,
    platform: str,          # "x" | "linkedin"
    search_terms: list[str],
    max_items: int,
    search_fn,
) -> int:
    """Search one social platform, classify new posts, alert early signals."""
    known_companies: list[Company] = [
        Company(
            source=r["source"], slug=r["slug"], name=r["name"],
            batch=r["batch"], one_liner=r["one_liner"] or "", url=r["url"] or "",
        )
        for r in conn.execute("SELECT * FROM companies")
    ]

    alerted = 0
    for term in search_terms:
        try:
            posts: list[SocialPost] = search_fn(
                client, settings.apify_token, term, max_items
            )
        except Exception as e:
            log(f"{platform}: search failed for {term!r}: {e}")
            continue
        log(f"{platform}: {len(posts)} posts for {term[:40]!r}")
        for post in posts:
            if not db.insert_signal(conn, post):
                continue  # exact post already seen — never re-alert
            status = classifier.classify_post(post, known_companies)
            db.update_signal_status(conn, post.external_id, status)
            if status == "early":
                send("early_signal", post, batch=classifier.batch_of(post))
                alerted += 1
    db.set_meta(conn, f"last_poll:{platform}", db.utcnow())
    return alerted


# ----------------------------------------------------------------- cycle ---

def cycle(settings: Settings, conn, send) -> dict:
    """One full sweep of all four sources. Returns per-source counts."""
    counts: dict[str, int | str] = {}

    with httpx.Client(headers={"User-Agent": "yc-radar/1.0"}) as client:
        # 1. YC directory (authoritative)
        try:
            counts["yc_directory"] = poll_directory(
                client, settings, conn, send, "yc_directory",
                fetch=lambda: yc_directory.fetch_companies(client),
            )
        except Exception as e:
            log(f"YC directory FAILED: {e}")
            counts["yc_directory"] = f"error: {e}"
            traceback.print_exc()

        # 2. a16z Speedrun directory (authoritative)
        try:
            counts["speedrun"] = poll_directory(
                client, settings, conn, send, "speedrun",
                fetch=lambda: speedrun.fetch_companies(client),
            )
        except Exception as e:
            log(f"Speedrun FAILED: {e}")
            counts["speedrun"] = f"error: {e}"

        # 3. X — early signals
        if settings.apify_token:
            try:
                counts["x"] = poll_social(
                    client, settings, conn, send, "x",
                    settings.x_search_terms, settings.x_max_items,
                    x_twitter.search_posts,
                )
            except Exception as e:
                log(f"X FAILED: {e}")
                counts["x"] = f"error: {e}"
        else:
            counts["x"] = "skipped (no APIFY_TOKEN)"

        # 4. LinkedIn — early signals
        if settings.apify_token:
            try:
                counts["linkedin"] = poll_social(
                    client, settings, conn, send, "linkedin",
                    settings.linkedin_search_terms, settings.linkedin_max_items,
                    linkedin.search_posts,
                )
            except Exception as e:
                log(f"LinkedIn FAILED: {e}")
                counts["linkedin"] = f"error: {e}"
        else:
            counts["linkedin"] = "skipped (no APIFY_TOKEN)"

    db.set_meta(conn, "last_cycle", db.utcnow())
    return counts


# ------------------------------------------------------------- sender ----

def make_sender(settings: Settings, conn):
    """
    Build the `send(kind, payload, **kw)` callback that delivers alerts to
    Slack (or dry-run logs when no token is configured). Shared by the
    persistent loop, one-shot polls, and the Pond server.
    """
    def send(kind: str, payload, **kw) -> None:
        if not settings.slack_bot_token:
            label = getattr(payload, "name", None) or getattr(payload, "author_name", "?")
            log(f"[DRY-RUN {kind}] {label}")
            return
        try:
            with httpx.Client() as client:
                ch = slack.resolve_channel(
                    client, settings.slack_bot_token, settings.slack_channel
                )
                if kind == "new_company":
                    slack.send_new_company(
                        client, settings.slack_bot_token, ch, payload
                    )
                elif kind == "early_signal":
                    slack.send_early_signal(
                        client, settings.slack_bot_token, ch, payload,
                        batch=kw.get("batch"),
                    )
            db.record_alert(
                conn, kind,
                payload.slug if kind == "new_company" else payload.external_id,
                None, True,
            )
            log(f"  → Slack alert sent ({kind}: "
                f"{getattr(payload, 'name', payload.author_name)})")
        except Exception as e:
            db.record_alert(
                conn, kind,
                getattr(payload, "slug", None) or payload.external_id, None, False,
            )
            log(f"  → Slack alert FAILED: {e}")

    return send


def run_forever(settings: Settings) -> None:
    """Persistent loop — this is what `python manage.py poll` runs."""
    conn = db.connect(settings.db_path)
    send = make_sender(settings, conn)

    log(f"Radar starting — poll every {settings.poll_interval_seconds/3600:.0f}h, "
        f"DB at {settings.db_path}")
    while True:
        log("=== cycle start ===")
        counts = cycle(settings, conn, send)
        log(f"cycle done: {counts}")
        log(f"sleeping {settings.poll_interval_seconds/3600:.0f}h...")
        time.sleep(settings.poll_interval_seconds)
