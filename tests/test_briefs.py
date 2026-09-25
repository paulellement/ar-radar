import datetime as dt

from anr_radar.briefs.generate import MAX_BIO_CHARS, build_prompt

FACTS = {
    "name": "Florence Road",
    "lastfm_listeners": 123456,
    "size_band": "50k-500k",
    "breakout_score": 97.9,
    "breakout_rank": 12,
    "listeners_7d_growth_pct": 4.21,
    "new_listeners_7d": 5000.0,
    "growth_window_days": 2,
    "is_provisional": True,
    "listeners_14d_growth_pct": 8.5,
    "plays_7d_growth_pct": 6.0,
    "chart_countries": ["Ireland"],
    "new_country_charts_14d": 1,
    "tags": ["indie rock", "irish"],
    "similar_names": ["Wunderhorse", "Just Mustard"],
    "bio_summary": "Florence Road are a band from Bray, Ireland.",
    "shows": [
        {
            "event_date": dt.date(2026, 10, 1),
            "venue": "The Drake Hotel",
            "city": "Toronto",
            "min_price": 25.0,
            "currency": "CAD",
        }
    ],
}


def test_prompt_contains_every_fact_and_the_rules():
    p = build_prompt(FACTS)
    for needle in [
        "Florence Road",
        "123,456",
        "+4.2%",
        "5,000 new listeners",
        "Ireland",
        "indie rock, irish",
        "Wunderhorse",
        "Bray, Ireland",
        "2026-10-01: The Drake Hotel, Toronto (from 25 CAD)",
        "PROVISIONAL",
        "Use ONLY the facts below",
        "### Caveats",
    ]:
        assert needle in p, needle


def test_missing_facts_are_explicit_not_blank():
    p = build_prompt({"name": "X", "breakout_score": 90.0, "growth_window_days": 7})
    assert "In country top-100 charts today: none" in p
    assert "Similar artists: none listed" in p
    assert "Upcoming shows:\n- none found" in p
    assert "Listener growth, 7 days: n/a" in p
    assert "PROVISIONAL" not in p


def test_long_bios_are_trimmed_at_a_word():
    p = build_prompt({**FACTS, "bio_summary": "word " * 1000})
    bio_line = next(line for line in p.splitlines() if line.startswith("Bio"))
    assert len(bio_line) < MAX_BIO_CHARS + 50 and bio_line.endswith("word...")


def test_show_without_price():
    shows = [{**FACTS["shows"][0], "min_price": None}]
    assert "The Drake Hotel, Toronto\n" in build_prompt({**FACTS, "shows": shows}) + "\n"
