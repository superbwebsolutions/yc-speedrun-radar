#!/usr/bin/env python3
"""
YC / Speedrun Launch Radar — management CLI.

This is the one command a non-technical operator ever needs:

  python manage.py doctor     — check every source is reachable & configured
  python manage.py poll      — start the persistent monitor (leave running)
  python manage.py poll --once — run exactly one sweep (testing)
  python manage.py test-alert — send a test alert to Slack
  python manage.py status    — what the bot currently knows
  python manage.py serve     — start the Pond Protocol server (optional)

No code edits are ever required: everything is configured via .env
(copy .env.example to get started).
"""
from __future__ import annotations

import sys

import httpx

from radar import db, slack
from radar.config import load_settings
from radar.poller import cycle, log, make_sender


DOCTOR_TIMEOUT = httpx.Timeout(20)


def cmd_doctor(_: dict) -> int:
    """Live-check every source and print a clear ✅/❌ table."""
    settings = load_settings()
    print("Running source checks...\n")
    ok = True

    with httpx.Client(timeout=DOCTOR_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "yc-radar/1.0"}) as client:
        # 1. YC directory (Algolia behind the page)
        try:
            from radar.sources import yc_directory
            page = client.get(yc_directory.PAGE_URL).text
            app, key = yc_directory.extract_algolia_credentials(page)
            sample = yc_directory.fetch_companies(client, app, key, max_companies=3)
            print(f"✅ YC Directory      — {len(sample)} sample companies fetched "
                  f"(e.g. {sample[0].name}, batch {sample[0].batch})")
        except Exception as e:
            ok = False
            print(f"❌ YC Directory      — {e}")

        # 2. Speedrun directory
        try:
            from radar.sources import speedrun
            comps = speedrun.fetch_companies(client, max_companies=3)
            print(f"✅ Speedrun Directory — {len(comps)} sample companies fetched "
                  f"(e.g. {comps[0].name}, cohort {comps[0].batch})")
        except Exception as e:
            ok = False
            print(f"❌ Speedrun Directory — {e}")

        # 3. X via Apify
        if settings.apify_token:
            try:
                from radar.sources import x_twitter
                posts = x_twitter.search_posts(
                    client, settings.apify_token, "YC", max_items=2
                )
                print(f"✅ X (Apify)          — {len(posts)} tweets fetched")
            except Exception as e:
                ok = False
                print(f"❌ X (Apify)          — {e}")
        else:
            print("⚠️  X (Apify)          — skipped: set APIFY_TOKEN in .env")

        # 4. LinkedIn via Apify
        if settings.apify_token:
            try:
                from radar.sources import linkedin
                posts = linkedin.search_posts(
                    client, settings.apify_token, "Y Combinator", max_items=2
                )
                print(f"✅ LinkedIn (Apify)   — {len(posts)} posts fetched")
            except Exception as e:
                ok = False
                print(f"❌ LinkedIn (Apify)   — {e}")
        else:
            print("⚠️  LinkedIn (Apify)   — skipped: set APIFY_TOKEN in .env")

        # 5. Slack
        if settings.slack_bot_token:
            try:
                r = client.get(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                ).json()
                if r.get("ok"):
                    print(f"✅ Slack             — token valid, workspace: "
                          f"{r.get('team', '?')}")
                    ch = slack.resolve_channel(
                        client, settings.slack_bot_token, settings.slack_channel
                    )
                    print(f"✅ Slack channel     — resolved {settings.slack_channel} → {ch}")
                else:
                    ok = False
                    print(f"❌ Slack             — token invalid: {r.get('error')}")
            except Exception as e:
                ok = False
                print(f"❌ Slack             — {e}")
        else:
            print("⚠️  Slack             — skipped: set SLACK_BOT_TOKEN in .env")

    print()
    if ok:
        print("All systems go. Start monitoring with:  python manage.py poll")
    else:
        print("Some sources failed. Fix the ❌ items (see .env.example).")
    return 0 if ok else 1


def cmd_status(_: dict) -> int:
    """Print what the monitor knows right now."""
    settings = load_settings()
    conn = db.connect(settings.db_path)
    s = db.stats(conn)
    print("Radar state:")
    print(f"  companies tracked   : {s['companies_total']} "
          f"(YC {s['companies_yc']} / Speedrun {s['companies_speedrun']})")
    print(f"  social posts seen   : {s['signals_seen']}")
    print(f"  early signals found  : {s['signals_early']}")
    print(f"  alerts sent to Slack : {s['alerts_sent']}")
    for key in ("last_cycle", "last_poll:yc_directory", "last_poll:speedrun",
                "last_poll:x", "last_poll:linkedin"):
        v = db.get_meta(conn, key)
        if v:
            print(f"  {key:22}: {v}")
    return 0


def cmd_poll(args: dict) -> int:
    """Run the monitor. --once for a single sweep (testing)."""
    settings = load_settings()
    if args.get("once"):
        conn = db.connect(settings.db_path)
        send = make_sender(settings, conn)
        counts = cycle(settings, conn, send)
        log(f"one-shot cycle complete: {counts}")
        return 0
    from radar.poller import run_forever
    run_forever(settings)
    return 0  # unreachable — run_forever loops forever


def cmd_test_alert(_: dict) -> int:
    """Send a test alert to Slack to prove the pipeline end-to-end."""
    settings = load_settings()
    if not settings.slack_bot_token:
        print("Set SLACK_BOT_TOKEN in .env first (see .env.example).")
        return 1
    conn = db.connect(settings.db_path)
    with httpx.Client() as client:
        ch = slack.resolve_channel(
            client, settings.slack_bot_token, settings.slack_channel
        )
        ts = slack.send_test_alert(client, settings.slack_bot_token, ch)
    db.record_alert(conn, "test", "test", ts, True)
    print(f"Test alert sent to {settings.slack_channel} (ts {ts}). "
          f"Go look at Slack!")
    return 0


def cmd_serve(_: dict) -> int:
    """Start the Pond Protocol server (for Pond health checks)."""
    settings = load_settings()
    if not settings.pond_enabled:
        print("Pond integration disabled (POND_ENABLED=false).")
        return 1
    import uvicorn
    from radar.pond import app
    print("Starting Pond Protocol server on 0.0.0.0:8000 ...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


COMMANDS = {
    "doctor": cmd_doctor,
    "status": cmd_status,
    "poll": cmd_poll,
    "test-alert": cmd_test_alert,
    "serve": cmd_serve,
}


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "doctor"
    rest = args[1:]
    flags = {a.lstrip("-"): True for a in rest if a.startswith("-")}
    if cmd not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[cmd](flags)


if __name__ == "__main__":
    raise SystemExit(main())
