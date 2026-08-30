"""Classifier tests — built from the task spec's example AND real posts
captured live during development (the Almanac/Rasyn posts were actual
results from the live Apify test runs)."""
from radar.models import Company, SocialPost
from radar.classifier import classify_post, batch_of, founder_score


ALMANAC = Company(
    source="yc_directory", slug="almanac", name="Almanac",
    batch="Summer 2026", one_liner="", url="https://almanac.example",
)


def make_post(text, **kw):
    kw.setdefault("platform", "x")
    kw.setdefault("external_id", kw.get("text", "x")[:30])
    kw.setdefault("post_url", "https://x.com/example/1")
    kw.setdefault("author_name", "Jane Doe")
    kw.setdefault("author_handle", "janedoe")
    return SocialPost(text=text, **kw)


def test_spec_example_is_early():
    """The exact example from the task brief must fire an early alert."""
    p = make_post("We got into YC S26! Excited to move to SF and start building.")
    assert classify_post(p, []) == "early"


def test_real_almanac_post_listed_when_in_directory():
    """Live-captured LinkedIn post: company already in YC directory → listed."""
    p = SocialPost(
        platform="linkedin", external_id="l1",
        text="Introducing Almanac payments. Today, Almanac (YC S26) is "
             "partnering with Agentcard (yc s26)",
        post_url="https://linkedin.com/p/1",
        author_name="Divit Sheth", author_handle="divit-sheth",
        author_bio="Founder @ Almanac (YC S26)",
    )
    assert classify_post(p, [ALMANAC]) == "listed"


def test_real_almanac_post_early_when_not_listed():
    p = SocialPost(
        platform="linkedin", external_id="l1",
        text="Introducing Almanac payments. Today, Almanac (YC S26) is "
             "partnering with Agentcard (yc s26)",
        post_url="https://linkedin.com/p/1",
        author_name="Divit Sheth", author_handle="divit-sheth",
        author_bio="Founder @ Almanac (YC S26)",
    )
    assert classify_post(p, []) == "early"


def test_congrats_reply_is_noise():
    assert classify_post(make_post("@janedoe congrats on YC S26!"), []) == "noise"


def test_howto_thread_is_noise():
    assert classify_post(make_post("How to get into YC — a thread"), []) == "noise"


def test_official_ycombinator_account_is_noise():
    p = make_post(
        "Introducing the YC S26 batch!", author_name="Y Combinator",
        author_handle="ycombinator",
    )
    assert classify_post(p, []) == "noise"


def test_speedrun_acceptance_is_early():
    p = make_post(
        "We got into a16z Speedrun! Building the next big thing in games",
        author_bio="Founder @ PlayLoop",
    )
    assert classify_post(p, []) == "early"


def test_company_account_reply_is_noise():
    """Live-captured: Rasyn (YC S26) replying to a user — not an announcement."""
    p = SocialPost(
        platform="x", external_id="t4",
        text="@aayush_learns let us know how it goes!",
        post_url="x", author_name="Rasyn (YC S26)", author_handle="rasyn_lab",
        author_bio="We're an AI chemistry company building models and agents.",
    )
    assert classify_post(p, []) == "noise"


def test_batch_extraction():
    assert batch_of(make_post("We're in YC S26!")) == "S26"
    assert batch_of(make_post("Backed by a16z Speedrun SR007")) == "SR007"


def test_founder_score_prioritises_founder_bios():
    founder = make_post("We got into YC S26!", author_bio="Founder @ Acme")
    fan = make_post("We got into YC S26!", author_bio="VC enthusiast")
    assert founder_score(founder) > founder_score(fan)
