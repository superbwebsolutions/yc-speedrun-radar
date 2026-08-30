"""
X / Twitter adapter — early founder signals, via Apify.

Uses the pay-per-event actor `apidojo/tweet-scraper` ($0.40 per 1,000
tweets). No X developer account, no OAuth, no API approval. Runs a plain
X advanced-search query (the same operators you'd type into X's search
box) and returns full tweet objects including author bio — which often
already says "(YC S26)" or "Founder @ ...".

Live-verified: caught real YC S26 company accounts posting within the hour.
"""
from __future__ import annotations

import time

import httpx

from ..models import SocialPost

ACTOR_ID = "apidojo/tweet-scraper"
API_BASE = "https://api.apify.com/v2"


def _run_input(search_term: str, max_items: int) -> dict:
    return {
        "searchTerms": [search_term],
        "sort": "Latest",
        "maxItems": max_items,
        "tweetLanguage": "en",
        "includeSearchTerms": True,
    }


def search_posts(
    client: httpx.Client,
    token: str,
    search_term: str,
    max_items: int = 100,
) -> list[SocialPost]:
    """Run one keyword search on X via Apify; return normalised posts."""
    # NOTE: in Apify API URLs, "owner/name" actor IDs are written "owner~name".
    actor_path = ACTOR_ID.replace("/", "~")
    # 1. Kick off the actor run
    start = client.post(
        f"{API_BASE}/acts/{actor_path}/runs",
        params={"token": token},
        json=_run_input(search_term, max_items),
        timeout=30,
    )
    start.raise_for_status()
    run = start.json()["data"]
    run_id = run["id"]

    # 2. Poll until finished (runs take a few seconds)
    deadline = time.time() + 120
    while True:
        r = client.get(
            f"{API_BASE}/actor-runs/{run_id}", params={"token": token}, timeout=30
        )
        r.raise_for_status()
        status = r.json()["data"]["status"]
        if status in ("SUCCEEDED",):
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify X run {run_id} ended with status {status}")
        if time.time() > deadline:
            raise TimeoutError(f"Apify X run {run_id} did not finish in 120s")
        time.sleep(2)

    # 3. Pull the dataset items
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
        text = it.get("text") or ""
        author = it.get("author") or {}
        posts.append(
            SocialPost(
                platform="x",
                external_id=str(it.get("id") or it.get("url") or hash(text)),
                text=text,
                post_url=it.get("url") or it.get("twitterUrl") or "",
                author_name=author.get("name") or "",
                author_handle=(author.get("userName") or "").lstrip("@"),
                author_url=author.get("url") or (
                    f"https://x.com/{author.get('userName', '')}".lstrip("@")
                    if author.get("userName") else ""
                ),
                author_bio=author.get("description") or "",
                created_at=it.get("createdAt") or "",
            )
        )
    return posts
