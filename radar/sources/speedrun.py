"""
a16z Speedrun adapter (authoritative feed #2).

Speedrun is a16z's accelerator (a16z Games / Andrew Chen, founded 2023,
$1M standard check, ~90 companies per cohort, SR001..SR007+). Its public
directory at speedrun.a16z.com is a Django app with an open JSON API —
keyless, no auth, just needs redirect-following + a cookie jar.

The record payload is a GTM goldmine: company profile + x_url +
linkedin_url + a nested founder_set with each founder's LinkedIn URL.
"""
from __future__ import annotations

import httpx

from ..models import Company

API_URL = (
    "https://speedrun.a16z.com/api/companies/companyparams/"
    "?offset={offset}&limit={limit}&team_size_min=1&ordering=name"
)
PAGE_SIZE = 100  # the API paginates (cap 16 on the site's own calls)

# Known cohorts, newest first (for display; SR007 was active in 2026).


def fetch_companies(
    client: httpx.Client, max_companies: int = 0
) -> list[Company]:
    """Page through the Speedrun API and return all companies."""
    companies: list[Company] = []
    offset = 0
    while True:
        url = API_URL.format(offset=offset, limit=PAGE_SIZE)
        resp = client.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        for rec in results:
            founders = [
                {
                    "name": " ".join(
                        part
                        for part in (f.get("first_name"), f.get("last_name"))
                        if part
                    ),
                    "linkedin_url": f.get("linkedin_url") or "",
                    "x_url": f.get("x_url") or "",
                }
                for f in rec.get("founder_set") or []
            ]
            companies.append(
                Company(
                    source="speedrun",
                    slug=rec["slug"],
                    name=rec.get("name") or rec["slug"],
                    batch=rec.get("cohort"),
                    one_liner=(
                        rec.get("preamble") or rec.get("description") or ""
                    )[:300],
                    url=rec.get("website_url")
                    or f"https://speedrun.a16z.com/companies/{rec['slug']}",
                    founders=founders,
                    raw=rec,
                )
            )
        offset += len(results)
        if max_companies and len(companies) >= max_companies:
            return companies[:max_companies]
        if offset >= data.get("count", 0):
            break
    return companies
