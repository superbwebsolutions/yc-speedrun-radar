"""
Early-signal classifier — the heart of the bot.

Takes a raw social post (X or LinkedIn) and decides, with cheap
deterministic rules (no LLM, no cost per decision):

  1. Is it announcing YC/Speedrun participation at all?
  2. If yes: is the author plausibly a FOUNDER (vs a random commenter,
     job-seeker, investor, or journalist)?
  3. If yes: is the company NOT yet in the authoritative directories?
     → ⚡ EARLY SIGNAL — founder announced before YC officially listed them.

Key design principle (from live testing): keyword search catches tons of
noise — replies from company accounts, third-party commentary, crypto
shill threads. The classifier's job is to keep only what a GTM person
would want to be woken up for.

Signals that a post is a genuine founder announcement:
  - "we got into YC" / "we're in YC S26" / "accepted to YC"
  - "thrilled to announce ... YC" / "backed by Y Combinator"
  - bio already says "Founder @ X (YC S26)" — strong founder evidence
"""
from __future__ import annotations

import re

from .models import Company, SocialPost

# --- Announcement phrases (case-insensitive, first-person plural/possessive)
ANNOUNCE_PATTERNS = [
    r"we\s+(?:just\s+)?got\s+into\s+(?:yc|y\s?combinator)",
    r"(?:we\s+)?(?:are|'re)\s+(?:in|part\s+of)\s+(?:yc|y\s?combinator)",
    r"got\s+accepted\s+(?:to|into)\s+(?:yc|y\s?combinator)",
    r"accepted\s+(?:to|into)\s+(?:yc|y\s?combinator|speedrun)",
    r"got\s+into\s+(?:yc|y\s?combinator)",
    r"got\s+into\s+(?:a16z\s+)?speedrun",
    r"(?:we\s+)?got\s+into\s+(?:a16z\s+)?speedrun",
    r"(?:thrilled|excited|honored|proud)\s+to\s+announce.{0,80}(?:yc|y\s?combinator|speedrun)",
    r"backed\s+by\s+(?:y\s?combinator|yc\b|a16z\s+speedrun)",
    r"(?:joining|joined)\s+(?:the\s+)?(?:yc|y\s?combinator)\s+(?:s\d{2}|w\d{2}|f\d{2}|batch)",
    r"y\s?combinator.{0,40}(?:s\d{2}|w\d{2}|f\d{2}).{0,60}(?:we|our|us)",
    r"\b(?:yc|speedrun)\s+(?:s\d{2}|w\d{2}|f\d{2}|sr\d{3})\b.{0,80}\b(?:we|our)\b",
    r"\bwe\s+.{0,40}\b(?:co-?founded|founding)\b.{0,60}(?:yc|y\s?combinator)",
]

# Batch mentions like "YC S26", "YC W27", "Speedrun SR007", "S26"
BATCH_RE = re.compile(
    r"\b(?:yc\s*)?(s\d{2}|w\d{2}|f\d{2})\b"
    r"|\bspeedrun\s*(sr\d{3}|\d{2})?\b"
    r"|\b(sr\d{3})\b",
    re.IGNORECASE,
)

# Strong founder evidence in bio or post text
FOUNDER_EVIDENCE = [
    r"\bfounder\b",
    r"\bco-?founder\b",
    r"\bceo\b",
    r"\bbuilding\b",
    r"\bi\s+build\b",
    r"\bwe\s+build\b",
    r"\bwe're\s+building\b",
    r"\bstartup\b",
    r"\bhead\s+of\b",
]

# Explicit noise — things a GTM person would NOT want to be alerted on.
NOISE_PATTERNS = [
    r"^@",                                   # X replies (start with @handle)
    r"\bapply(ing)?\s+(to|for)\b",           # aspirational: "applying to YC"
    r"\bapplying\s+for\s+YC\b",
    r"\bhiring\b",
    r"\bwe're\s+hiring\b",
    r"\bjobs?\b",
    r"\bopen\s+to\s+work\b",
    r"\blooking\s+for\s+(?:a\s+)?(?:job|work|intern)",
    r"\bcongrats?(?:ulations)?\b",           # bystander congratulations
    r"\binterview\s+(?:request|tips?)\b",
    r"\bhow\s+to\s+get\s+into\b",
    r"\bodds\s+of\s+getting\s+into\b",
    # rejection posts (live-captured): "didn't make it", "we were rejected"
    r"didn'?t\s+(?:get\s+in(?:to)?|make\s+it|get\s+accepted|get\s+into)",
    r"\bwe\s+(?:were\s+)?rejected\b",
    # question/commentary openers: "who got accepted into YC?"
    r"^(?:who|anyone|anybody|everyone|why|how\s+many)\b.{0,60}accepted",
    r"\b(?:interview|prep)\s+for\s+YC\b",
    r"\bYC\s+(?:interviews?|application)\b",
    r"\bfellowship\b",
    r"\bscholarship\b",
    r"\baggregat(?:e|or)\b",
    r"\bcurated\s+list\b",
    r"\bjob\s+board\b",
    r"\bmeme\b",
]

# Directories' own accounts & known official broadcast handles (noise for us)
OFFICIAL_ACCOUNTS = {
    "ycombinator", "y combinator", "y combinator official",
    "a16z", "andreessen horowitz", "a16z games", "speedrun",
}


def _norm(s: str) -> str:
    """Lowercase + strip + fold curly quotes to ASCII (X posts use U+2019)."""
    s = (s or "").lower().strip()
    return s.replace("\u2019", "'").replace("\u2018", "'")


def mentions_batch(text: str) -> str | None:
    """Return the matched batch token (e.g. 'S26', 'SR007', 'Speedrun') or None."""
    m = BATCH_RE.search(_norm(text))
    if not m:
        return None
    token = m.group(1) or m.group(2) or m.group(3) or m.group(0)
    token = token.strip()
    if re.fullmatch(r"s\d{2}|w\d{2}|f\d{2}|sr\d{3}", token):
        return token.upper()          # S26, W27, SR007 — display format
    return token.capitalize()         # "Speedrun"


def is_announcement(text: str) -> bool:
    """Does the post announce YC/Speedrun participation?"""
    t = _norm(text)
    return any(re.search(p, t) for p in ANNOUNCE_PATTERNS)


def is_noise(text: str) -> bool:
    """Does the post match known noise patterns (replies, congrats, hiring...)?"""
    t = _norm(text)
    return any(re.search(p, t) for p in NOISE_PATTERNS)


def founder_score(post: SocialPost) -> int:
    """0-100 score: how strongly the author looks like a real founder."""
    bio = _norm(post.author_bio)
    text = _norm(post.text)
    score = 0
    if re.search(r"\bfounder\b|\bco-?founder\b|\bceo\b", bio):
        score += 50
    if re.search(r"\b(?:yc|y\s?combinator)\s*(?:s|w|f)\d{2}\b", bio):
        score += 20  # bio says "(YC S26)" — company already public? could be
    if any(re.search(p, text) for p in FOUNDER_EVIDENCE):
        score += 30
    if re.search(r"\bwe\b.{0,30}\b(?:build|building|launched|shipping)\b", text):
        score += 20
    return min(score, 100)


def classify_post(
    post: SocialPost,
    known_companies: list[Company],
    early_threshold: int = 60,
) -> str:
    """
    Decide a post's status. Returns one of:
      'early'     — ⚡ founder announcement, company NOT in directories → ALERT
      'listed'    — announcement from an already-listed company  → no alert (dup)
      'noise'     — not a founder announcement / bystander chatter → discard
    """
    text = post.text
    if not text or not text.strip():
        return "noise"

    # 1. Must announce YC/Speedrun participation in the first person.
    if not is_announcement(text):
        # Bio-based fallback: account bio says "(YC S26)" AND the post is
        # a launch/product post (not a reply) — a company account posting
        # about its product with a YC-bio is still a valid GTM target,
        accounts_bio_y = re.search(
            r"\b(?:yc|y\s?combinator)\s*(?:s|w|f)\d{2}\b", _norm(post.author_bio)
        )
        if not (accounts_bio_y and not text.lstrip().startswith("@")):
            return "noise"

    # 2. Filter out obvious noise first.
    if is_noise(text):
        return "noise"

    # 3. Official accounts (YC/a16z's own broadcast posts) are never early
    #    signals — they're the "official announcement" we're trying to beat.
    handle = _norm(post.author_handle)
    name = _norm(post.author_name)
    if handle in OFFICIAL_ACCOUNTS or name in OFFICIAL_ACCOUNTS:
        return "noise"

    # 3b. Third-party commentary (X handles that aren't the founder) —
    # keep only if bio+text give strong founder evidence.
    score = founder_score(post)
    if score < early_threshold and post.platform == "x" and "@" in text[:5]:
        return "noise"

    # 4. Company cross-check: is the author's company already listed?
    #    Live-testing showed naive substring matching is too loose ("Auto"
    #    matches "automotive"); we use word-bounded phrase matching on the
    #    post text, squashed-handle token matching on name+bio (so bio
    #    "cofounder @legionhealth" matches company "Legion Health"), and a
    #    website-domain check on the bio.
    a_toks = _author_tokens(post)
    bio_norm = _norm(post.author_bio)
    text_norm = _norm(post.text)
    for company in known_companies:
        cname = _norm(company.name)
        if not cname:
            continue
        # (a) company name (word-bounded) appears in the post text
        if re.search(r"\b" + re.escape(cname) + r"\b", text_norm):
            return "listed"
        # (b) squashed name matches an author name/bio token or the handle
        #     e.g. "RunInfra" == author "RunInfra (YC F26)", "Legion Health"
        #     == bio token "@legionhealth"
        cq = _squash(cname)
        if len(cq) >= 5 and cq in a_toks:
            return "listed"
        # (c) company website domain mentioned in the author bio
        dom = _company_website_domain(company)
        if dom and dom in bio_norm:
            return "listed"
    return "early"


def _squash(s: str) -> str:
    """Lowercase, strip everything that isn't a letter/digit."""
    return re.sub(r"[^a-z0-9]", "", _norm(s))


def _author_tokens(post: SocialPost) -> set[str]:
    """Alphanumeric tokens from author name + bio, plus the bare handle."""
    toks: set[str] = set()
    for w in re.split(r"[^a-z0-9]+", _norm(post.author_name + " " + post.author_bio)):
        if len(w) > 2:
            toks.add(w)
    h = _norm(post.author_handle)
    if h:
        toks.add(h)
    return toks


def _company_website_domain(company: Company) -> str:
    """Company's real website domain (YC keeps it in raw.website,
    Speedrun in raw.website_url) — empty string when unknown."""
    raw = company.raw or {}
    site = raw.get("website") or raw.get("website_url") or ""
    if not site:
        url = company.url or ""
        if "ycombinator.com" not in url and "speedrun.a16z.com" not in url:
            site = url
    return site.replace("https://", "").replace("http://", "").split("/")[0].lower()


def batch_of(post: SocialPost) -> str | None:
    """Best-effort batch token from the post text or author bio."""
    return mentions_batch(post.text) or mentions_batch(post.author_bio)
