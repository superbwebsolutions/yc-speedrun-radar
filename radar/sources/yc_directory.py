"""
YC Directory adapter (authoritative feed #1).

ycombinator.com/companies is an Algolia-powered React app. The page itself
renders client-side, but the underlying Algolia index is public: the page
embeds a scoped search key in `window.AlgoliaOpts`. We fetch that page,
extract app id + key (self-refreshing: if YC ever rotates the key, the next
poll just picks up the new one — no config, no breakage), and query the
Algolia API directly for clean, structured, complete JSON.

Why this matters: the directory is the SOURCE OF TRUTH for "is this a real
YC company", which the early-signal classifier needs for its cross-check.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

import httpx

from ..models import Company

PAGE_URL = "https://www.ycombinator.com/companies"
ALGOLIA_QUERY_URL = "https://{app_id}-dsn.algolia.net/1/indexes/*/queries"
INDEX = "YCCompany_production"
PAGE_SIZE = 1000  # Algolia max per page

# Matches e.g.  window.AlgoliaOpts = {"app":"45BWZJ1SGC","key":"..."} ;
_OPTS_RE = re.compile(
    r'window\.AlgoliaOpts\s*=\s*(\{.*?\})\s*;?', re.DOTALL
)


def extract_algolia_credentials(html: str) -> tuple[str, str]:
    """Pull (app_id, api_key) out of the raw companies page HTML."""
    m = _OPTS_RE.search(html)
    if not m:
        raise RuntimeError(
            "Could not find window.AlgoliaOpts on the YC companies page — "
            "YC's frontend may have changed. Try a fresh page fetch."
        )
    import json
    opts = json.loads(m.group(1))
    return opts["app"], opts["key"]


def _decode_key_scope(api_key: str) -> str:
    """Algolia secured keys are base64-encoded scopes — handy for debugging."""
    import base64
    try:
        return base64.b64decode(api_key).decode("utf-8", errors="replace")
    except Exception:
        return "<not base64>"


def fetch_companies(
    client: httpx.Client,
    app_id: str | None = None,
    api_key: str | None = None,
    max_companies: int = 0,
) -> list[Company]:
    """
    Return EVERY company in the YC directory (all ~6,200, all 50 batches).

    The page's secured Algolia key caps a single query at 1,000 hits, so
    we enumerate the `batch` facet first (one query), then run one
    facet-filtered query per batch (each batch is well under 1,000) —
    the exact same access pattern the public YC website itself uses
    when you filter by batch in the UI.
    """
    if not (app_id and api_key):
        page = client.get(PAGE_URL, headers={"User-Agent": "yc-radar/1.0"})
        page.raise_for_status()
        app_id, api_key = extract_algolia_credentials(page.text)

    def query(params: str) -> dict:
        body = {
            "requests": [
                {
                    "indexName": INDEX,
                    "params": params,
                }
            ]
        }
        resp = client.post(
            ALGOLIA_QUERY_URL.format(app_id=app_id),
            json=body,
            headers={
                "X-Algolia-Application-Id": app_id,
                "X-Algolia-API-Key": api_key,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["results"][0]

    base_attrs = (
        "&attributesToRetrieve=slug,name,batch,one_liner,launched_at,"
        "website,region,tags,top_comp"
    )

    # 1. Enumerate all batches via the facet (1 cheap query, 0 hits).
    facet_res = query(
        "hitsPerPage=0&facets=%5B%22batch%22%5D" + base_attrs
    )
    batches = sorted(
        (facet_res.get("facets") or {}).get("batch", {}).keys(),
        reverse=True,  # newest batches first
    )
    if not batches:
        raise RuntimeError("No batch facets returned from YC directory")

    companies: dict[str, Company] = {}
    for batch in batches:
        if max_companies and len(companies) >= max_companies:
            break
        page_no = 0
        while True:
            encoded = json.dumps([f"batch:{batch}"])  # URL-safe via params body
            res = query(
                f"hitsPerPage=1000&page={page_no}"
                f"&facetFilters={quote(encoded)}"
                + base_attrs
            )
            hits = res.get("hits", [])
            for hit in hits:
                slug = hit["slug"]
                if slug in companies:
                    continue
                companies[slug] = Company(
                    source="yc_directory",
                    slug=slug,
                    name=hit.get("name") or slug,
                    batch=hit.get("batch"),
                    one_liner=hit.get("one_liner") or "",
                    url=f"https://www.ycombinator.com/companies/{slug}",
                    raw=hit,
                )
            if page_no + 1 >= res.get("nbPages", 1) or not hits:
                break
            page_no += 1

    out = list(companies.values())
    # Newest first (matches the directory's own default ordering closely).
    out.sort(key=lambda c: (c.raw or {}).get("launched_at") or 0, reverse=True)
    return out


def newest_batch(companies: list[Company]) -> str | None:
    """Most recent batch name seen in the directory (for display)."""
    batches = [c.batch for c in companies if c.batch]
    return batches[0] if batches else None
