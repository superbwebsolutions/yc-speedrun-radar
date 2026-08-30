"""
Central configuration.

Every knob is an environment variable (see .env.example) so a non-technical
operator never edits code — they edit one file, or set vars in their hosting
panel. Only two tokens are ever required:

  SLACK_BOT_TOKEN  – from the Slack app you install (starts with xoxb-)
  APIFY_TOKEN      – from your free Apify account; covers BOTH the
                     X/Twitter and the LinkedIn scrapers.

The two directories (YC + a16z Speedrun) are public and keyless and need
zero configuration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load a local .env if present (real environment variables still win).
load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


# Default keyword queries for early-signal hunting. These are plain
# X/LinkedIn search strings — operators like OR and "quotes" work.
# Tune them freely via env vars without touching code.
DEFAULT_X_SEARCH = (
    '"got into YC" OR "accepted to YC" OR "we got into Y Combinator" '
    'OR "YC S26" OR "YC W27" OR "backed by Y Combinator" OR "a16z Speedrun"'
)
DEFAULT_LINKEDIN_SEARCH = (
    '"got into YC" OR "got into Y Combinator" OR "YC S26" OR "a16z Speedrun" '
    'OR "backed by Y Combinator"'
)


@dataclass
class Settings:
    # ------------------------------------------------------------- Slack ---
    slack_bot_token: str = ""        # xoxb-... (required to deliver alerts)
    slack_channel: str = "#yc-radar"  # "#name" or a raw channel ID

    # ------------------------------------------------------------- Apify ---
    apify_token: str = ""            # covers both X and LinkedIn scrapers

    # ------------------------------------------------------ Directories ---
    # Public + keyless, kept as constants for visibility. The YC Algolia key
    # is auto-extracted from the live YC page at runtime — nothing to set.
    yc_companies_page: str = "https://www.ycombinator.com/companies"
    speedrun_api_url: str = (
        "https://speedrun.a16z.com/api/companies/companyparams/"
        "?offset={offset}&limit={limit}&team_size_min=1&ordering=name"
    )

    # ------------------------------------------------------------ Polling --
    poll_interval_seconds: int = 8 * 3600   # task spec: 8h cadence is fine
    x_poll_interval_seconds: int = 8 * 3600
    linkedin_poll_interval_seconds: int = 24 * 3600  # cost control (~$0.15/day)

    # First run marks everything as "already known" (no 200-alert spam),
    # but still posts this many newest listings so a fresh install
    # immediately shows what alerts look like.
    cold_start_alerts: int = 3

    # ---------------------------------------------------- Social queries ---
    x_search_terms: list[str] = field(default_factory=lambda: [DEFAULT_X_SEARCH])
    linkedin_search_terms: list[str] = field(
        default_factory=lambda: [DEFAULT_LINKEDIN_SEARCH]
    )
    x_max_items: int = 100           # tweets fetched per poll (~$0.04 max)
    linkedin_max_items: int = 30    # posts fetched per poll ($0.15 max)

    # ------------------------------------------------------------- State ---
    db_path: Path = REPO_ROOT / "data" / "radar.db"

    # ---------------------------------------------------------- Pond ------
    pond_enabled: bool = True        # expose Pond Protocol endpoints (optional)
    pond_access_key: str = ""        # set when you register the agent on Pond


def load_settings() -> Settings:
    """Build a Settings object purely from the environment."""
    db_path = Path(_env("DB_PATH", str(Settings.db_path)))
    return Settings(
        slack_bot_token=_env("SLACK_BOT_TOKEN"),
        slack_channel=_env("SLACK_CHANNEL", "#yc-radar"),
        apify_token=_env("APIFY_TOKEN"),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 8 * 3600),
        x_poll_interval_seconds=_env_int("X_POLL_INTERVAL_SECONDS", 8 * 3600),
        linkedin_poll_interval_seconds=_env_int(
            "LINKEDIN_POLL_INTERVAL_SECONDS", 24 * 3600
        ),
        cold_start_alerts=_env_int("COLD_START_ALERTS", 3),
        x_search_terms=_env_list("X_SEARCH_TERMS", [DEFAULT_X_SEARCH]),
        linkedin_search_terms=_env_list(
            "LINKEDIN_SEARCH_TERMS", [DEFAULT_LINKEDIN_SEARCH]
        ),
        x_max_items=_env_int("X_MAX_ITEMS", 100),
        linkedin_max_items=_env_int("LINKEDIN_MAX_ITEMS", 30),
        db_path=db_path,
        pond_enabled=_env("POND_ENABLED", "true").lower() != "false",
        pond_access_key=_env("POND_ACCESS_KEY"),
    )
