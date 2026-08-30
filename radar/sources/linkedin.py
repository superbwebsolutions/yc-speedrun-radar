"""
LinkedIn adapter — early founder signals, via Apify.

Uses `apimaestro/linkedin-posts-search-scraper-no-cookies` ($0.005 per
post). No LinkedIn API partner program, no login, no cookies — the actor
scrapes LinkedIn's public search. Supports date filters (past-24h /
past-week), keyword search with X-style OR/"quotes" operators, and
returns the author's name + headline + profile URL.

Live-verified: caught a YC S26 founder's launch post 26 minutes after
it was published.
"""
from __future__ import annotations

import time

import httpx

from ..models import SocialPost

ACTOR_ID = "apimaestro/linkedin-posts-search-scraper-no-cookies"
API_BASE = "https://api.apify.com/v2"


def _run_input(search_term: str, max_items: int) -> dict:
    return {
        "keyword": search_term,
        "sort_type": "date_posted",
        "date_filter": "past-24h",
        "limit": min(max_items, 50),
        "total_posts": max_items,
    }


def search_posts(
    client: httpx.Client,
    token: str,
    search_term: str,
    max_items: int = 30,
) -> list[SocialPost]:
    """Run one keyword search on LinkedIn via Apify; return normalised posts."""
    # NOTE: in Apify API URLs, "owner/name" actor IDs are written "owner~name".
    actor_path = ACTOR_ID.replace("/", "~")
    start = client.post(
        f"{API_BASE}/acts/{actor_path}/runs",
        params={"token": token},
        json=_run_input(search_term, max_items),
        timeout=30,
    )
    start.raise_for_status()
    run = start.json()["data"]
    run_id = run["id"]

    deadline = time.time() + 120
    while True:
        r = client.get(
            f"{API_BASE}/actor-runs/{run_id}", params={"token": token}, timeout=30
        )
        r.raise_for_status()
        status = r.json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify LinkedIn run {run_id} ended with status {status}")
        if time.time() > deadline:
            raise TimeoutError(f"Apify LinkedIn run {run_id} did not finish in 120s")
        time.sleep(2)

    default_id = run.get("defaultDatasetId")
    items = []
    offset = 0
    while True:
        r = client.get(
            f"{API_BASE}/datasets/{default_id}/items",
            params={"token": token, "offset": offset, "limit": 100},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        items.extend(batch)
        if len(batch) < 100:
            break
        offset += 100

    posts = []
    for it in items:
        author = it.get("author") or {}
        posts.append(
            SocialPost(
                platform="linkedin",
                external_id=str(
                    it.get("activity_id") or it.get("post_url") or ""
                ),
                text=it.get("text") or "",
                post_url=it.get("post_url") or "",
                author_name=author.get("name") or "",
                author_handle=(author.get("profile_url") or "").rstrip("/").split("/")[-1],
                author_url=author.get("profile_url") or "",
                author_bio=author.get("headline") or "",
                created_at=(it.get("posted_at") or {}).get("date") or "",
            )
        )
    return posts
