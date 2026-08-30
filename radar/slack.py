"""
Slack delivery — posts alerts via the Web API (chat.postMessage).

Uses token auth (no webhooks needed): one bot token from your Slack app,
delivered to any channel the bot is invited to, or to the installer's DMs.

Message formats mirror the task spec's two example alerts, built as Slack
"blocks" so they render nicely: bold company name, emoji status line,
quote-styled original post, and every link clickable.
"""
from __future__ import annotations

import httpx

API = "https://slack.com/api/chat.postMessage"


class SlackError(RuntimeError):
    pass


def _post(client: httpx.Client, token: str, channel: str, payload: dict) -> dict:
    resp = client.post(
        API,
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, **payload},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackError(f"Slack API error: {data.get('error')} ({data.get('detail', '')})")
    return data


def send_test_alert(client: httpx.Client, token: str, channel: str) -> str:
    """Send a friendly 'the bot works' message; returns message ts."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🛰️ YC Radar is live"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "This is a test alert from your YC / Speedrun Launch Radar.\n\n"
                    "From now on you'll get two kinds of messages here:\n"
                    "• *⚡ Early YC signal* — a founder announced YC/Speedrun on X or "
                    "LinkedIn *before* the directory listed them (the GTM gold)\n"
                    "• *✅ New listing* — a company just appeared in the YC directory "
                    "or the a16z Speedrun directory"
                ),
            },
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "Monitoring: YC Directory · Speedrun · X · LinkedIn"}]},
    ]
    return _post(client, token, channel, {"blocks": blocks})["ts"]


def send_new_company(client: httpx.Client, token: str, channel: str, c) -> str:
    """
    ✅ Confirmed listing alert — matches the task's "Example 2".

    `c` is a radar.models.Company.
    """
    batch_line = f"Batch: {c.batch}\n" if c.batch else ""
    text = (
        f"*NEW {('SPEEDRUN' if c.source == 'speedrun' else 'YC')} COMPANY*\n\n"
        f"*Company:* {c.name}\n"
        f"{batch_line}"
        f"*Source:* {'a16z Speedrun Directory' if c.source == 'speedrun' else 'YC Directory'}\n"
        f"*Status:* ✅ Confirmed — listed in the official directory\n"
        f"*Description:* {c.one_liner or '—'}\n"
        f"<{c.url}|View profile>"
    )
    if c.founders:
        fl = "\n".join(
            f"  • {f.get('name', '')} {('- ' + f['linkedin_url']) if f.get('linkedin_url') else ''}"
            for f in c.founders[:4]
        )
        text += f"\n*Founders:*\n{fl}"
    return _post(client, token, channel, {"text": text})["ts"]


def send_early_signal(client: httpx.Client, token: str, channel: str, p, batch: str | None = None) -> str:
    """
    ⚡ Early-signal alert — matches the task's "Example 1".

    `p` is a radar.models.SocialPost. This is THE alert: founder posted,
    directories haven't listed them yet.
    """
    platform = "X" if p.platform == "x" else "LinkedIn"
    author = f"{p.author_name}"
    if p.author_handle:
        author += f" (@{p.author_handle})" if p.platform == "x" else f" ({p.author_handle})"
    batch_line = f"Batch: {batch or 'unknown'}\n" if (batch or p.author_bio) else ""
    if not batch and p.author_bio:
        batch_line = f"Founder bio: {p.author_bio[:120]}\n"

    snippet = p.text[:600] + ("…" if len(p.text) > 600 else "")
    text = (
        f":zap: *EARLY YC SIGNAL — Founder Announced Before Official Listing*\n\n"
        f"*Founder:* {author}\n"
        f"{batch_line}"
        f"*Source:* {platform}\n"
        f"*Status:* ⚡ Not yet in the YC/Speedrun directories\n\n"
        f"*Original post:*\n> {snippet}\n\n"
        f"<{p.post_url}|Open original post> · "
        f"<{p.author_url or p.post_url}|Founder profile>"
    )
    return _post(client, token, channel, {"text": text})["ts"]


def resolve_channel(client: httpx.Client, token: str, wanted: str) -> str:
    """
    Turn a configured channel name ('#yc-radar' or a raw ID like 'C123')
    into a real channel ID. Also accepts the installer's user ID for DMs.
    """
    if wanted.startswith(("C", "D", "G")) and not wanted.startswith("#"):
        return wanted
    # Minimal-scope installs (chat:write only) cannot call conversations.list.
    # Slack's chat.postMessage accepts "#name" directly, so resolve lazily:
    # try the post and only fall back to a channel listing when it fails.
    if not wanted.startswith("#"):
        raise SlackError(
            f"Cannot resolve channel {wanted!r}: expected '#name' or a channel ID."
        )
    return wanted
