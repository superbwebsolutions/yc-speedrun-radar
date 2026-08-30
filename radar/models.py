"""
Shared data shapes.

One small vocabulary used by every layer: `Company` is what the two
authoritative directories produce, `SocialPost` is what the X/LinkedIn
scrapers produce. Everything downstream (classifier, Slack, state DB)
speaks in these two types, which keeps adapters swappable — adding a new
platform later only means producing SocialPost objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Company:
    """A company as listed by an authoritative directory (YC or Speedrun)."""

    source: str              # "yc_directory" | "speedrun"
    slug: str                # stable unique id within the source
    name: str
    batch: str | None        # "Summer 2026" (YC) | "SR007" (Speedrun)
    one_liner: str           # short description
    url: str                 # website or profile URL
    founders: list = field(default_factory=list)   # [{name, linkedin_url}, ...]
    raw: dict = field(default_factory=dict)        # untouched source record


@dataclass
class SocialPost:
    """A post harvested from X or LinkedIn (normalised shape)."""

    platform: str            # "x" | "linkedin"
    external_id: str         # tweet id / LinkedIn activity urn — dedupe key
    text: str
    post_url: str
    author_name: str = ""
    author_handle: str = ""  # X handle (no @) or LinkedIn vanity slug
    author_url: str = ""
    author_bio: str = ""     # X bio / LinkedIn headline
    created_at: str = ""      # best-effort original post timestamp
