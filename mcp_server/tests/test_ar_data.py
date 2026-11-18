import datetime as dt

import duckdb
import pandas as pd
import pytest

import ar_data
from ar_data import QueryError
from conftest import AS_OF, fixture_tables
from export_snapshot import write_snapshot

INJECTION = "x'; DROP TABLE artist_signals; --"


def names(result, key="artists", field="name"):
    return [r[field] for r in result[key]]


# --- ranking --------------------------------------------------------------------------------


def test_ranking_sorted_and_excludes_alias_unscored_and_superstars(db):
    result = ar_data.top_breakout_artists(db=db)
    assert names(result) == [
        "Florence Road", "CHXRRY", "Phoebe Bridgers", "Maple Kid", "Spoon", "Spoons",
    ]  # fmt: skip
    scores = [a["breakout_score"] for a in result["artists"]]
    assert scores == sorted(scores, reverse=True)
    assert result["as_of_date"] == "2026-11-10"


def test_include_superstars(db):
    result = ar_data.top_breakout_artists(include_superstars=True, db=db)
    assert "Big Star Canada" in names(result)
    assert "JAŸ-Z" not in names(result)  # aliases are never included


def test_country_filter_uses_chart_then_tag(db):
    for spelling in ("Canada", "ca", "Canadian"):
        result = ar_data.top_breakout_artists(country=spelling, db=db)
        assert result["filters"]["country"] == "Canada"
        assert [(a["name"], a["country_evidence"]) for a in result["artists"]] == [
            ("Maple Kid", "chart"),
            ("Spoons", "tag"),
        ]


def test_unknown_country_is_an_error_with_hint(db):
    with pytest.raises(QueryError) as e:
        ar_data.top_breakout_artists(country="Atlantis", db=db)
    assert "Canada (CA)" in e.value.hint


def test_genre_filter_is_case_insensitive(db):
    result = ar_data.top_breakout_artists(genre="INDIE POP", db=db)
    assert names(result) == ["CHXRRY", "Phoebe Bridgers", "Maple Kid"]


def test_size_band_filter(db):
    assert names(ar_data.top_breakout_artists(size_band="10K-50K", db=db)) == [
        "Florence Road",
        "Maple Kid",
    ]
    with pytest.raises(QueryError):
        ar_data.top_breakout_artists(size_band="medium", db=db)


def test_limit_is_capped_at_50(tmp_path):
    tables = fixture_tables()
    one = tables["artist_signals"].iloc[[1]]  # Florence Road
    many = pd.concat([one] * 80, ignore_index=True)
    many["artist_key"] = [f"k{i}" for i in range(80)]
    many["name"] = [f"Artist {i}" for i in range(80)]
    tables["artist_signals"] = many
    path = tmp_path / "big.duckdb"
    write_snapshot(tables, path, "test")
    assert ar_data.top_breakout_artists(limit=500, db=path)["count"] == 50
    assert ar_data.top_breakout_artists(limit=0, db=path)["count"] == 1


def test_provisional_rows_get_a_note(db):
    result = ar_data.top_breakout_artists(country="Canada", db=db)
    assert any("provisional" in n for n in result["notes"])


# --- lookup ---------------------------------------------------------------------------------


def test_find_artist_exact_is_case_and_accent_insensitive(db):
    match = ar_data.find_artist("chxrry", db=db)
    assert match["matched"] and match["method"] == "exact"
    assert match["artist"]["name"] == "CHXRRY"


def test_find_artist_typo_matches(db):
    match = ar_data.find_artist("Phobe Bridgers", db=db)
    assert match["matched"] and match["method"] == "fuzzy"
    assert match["artist"]["name"] == "Phoebe Bridgers"


def test_find_artist_exact_beats_lookalike(db):
    assert ar_data.find_artist("Spoon", db=db)["artist"]["name"] == "Spoon"


def test_find_artist_short_or_weak_match_asks_instead_of_guessing(db):
    for query in ("Spon", "Zzyzx Quartet", "Florence Rd"):
        match = ar_data.find_artist(query, db=db)
        assert match["matched"] is False
        assert len(match["alternatives"]) == 3


def test_find_artist_empty_name(db):
    with pytest.raises(QueryError):
        ar_data.find_artist("  ", db=db)


# --- profile --------------------------------------------------------------------------------


def test_profile_has_signals_trend_brief_and_shows(db):
    p = ar_data.artist_profile("CHXRRY", db=db)
    assert p["signals"]["breakout_score"] == 97.0
    assert p["trend"]["pattern"] == "concentrated"
    assert p["trend"]["largest_one_day_share"] == 1.0
    assert p["scout_brief"]["label_mentioned"] is True
    assert p["scout_brief"]["generated_at"] == "2026-11-09T06:00:00"  # tz-aware in, UTC out
    assert [s["venue"] for s in p["upcoming_shows"]] == ["Lee's Palace", "Petit Campus"]
    assert len(p["daily_listeners"]) == 10


def test_profile_steady_growth_and_no_past_shows(db):
    p = ar_data.artist_profile("maple kid", db=db)
    assert p["trend"]["pattern"] == "steady"
    assert p["scout_brief"] is None
    assert [s["venue"] for s in p["upcoming_shows"]] == ["Horseshoe Tavern"]  # past show dropped


def test_profile_without_history(db):
    assert ar_data.artist_profile("Spoon", db=db)["trend"]["pattern"] == "insufficient_history"


def test_profile_unmatched_returns_alternatives(db):
    p = ar_data.artist_profile("Spon", db=db)
    assert p["matched"] is False and p["alternatives"]


# --- shows ----------------------------------------------------------------------------------


def test_shows_window_excludes_past_and_far_future(db):
    result = ar_data.upcoming_shows(db=db)
    assert names(result, "shows", "artist_name") == [
        "Florence Road",
        "CHXRRY",
        "Maple Kid",
        "Spoon",
    ]  # noqa: E501
    assert result["window"] == {"from": "2026-11-10", "to": "2026-12-10"}
    assert "Phoebe Bridgers" in names(
        ar_data.upcoming_shows(days_ahead=90, db=db), "shows", "artist_name"
    )  # noqa: E501


def test_shows_max_price_keeps_unknown_prices_flagged(db):
    result = ar_data.upcoming_shows(city="Toronto", max_price=30, db=db)
    shows = {s["artist_name"]: s for s in result["shows"]}
    assert set(shows) == {"CHXRRY", "Maple Kid"}  # Spoon ($60) dropped
    assert shows["CHXRRY"]["price_known"] is False and shows["CHXRRY"]["min_price"] is None
    assert shows["Maple Kid"]["price_known"] is True
    assert (result["total_matches"], result["priced_matches"]) == (2, 1)
    priced = ar_data.upcoming_shows(city="Toronto", max_price=30, require_price=True, db=db)
    assert names(priced, "shows", "artist_name") == ["Maple Kid"]


def test_shows_city_matches_every_spelling(db):
    # Ticketmaster has both "Montréal" and "Montreal"; either query must find both.
    for query in ("montreal", "MONTRÉAL"):
        result = ar_data.upcoming_shows(city=query, days_ahead=60, db=db)
        assert result["filters"]["city"] == ["Montreal", "Montréal"]
        assert names(result, "shows", "artist_name") == ["Florence Road", "CHXRRY"]
    assert ar_data.upcoming_shows(city="toronto", db=db)["filters"]["city"] == "Toronto"


def test_stale_snapshot_never_lists_past_shows(db, monkeypatch):
    # Snapshot is from AS_OF; pretend it's now 12 days later. Shows on days 5 and 10 are over.
    monkeypatch.setattr(ar_data, "_today", lambda: AS_OF + dt.timedelta(days=12))
    result = ar_data.upcoming_shows(db=db)
    assert result["window"]["from"] == "2026-11-22"
    assert names(result, "shows", "artist_name") == ["Florence Road", "CHXRRY"]
    assert any("12 days old" in n for n in result["notes"])
    assert any("12 days old" in n for n in ar_data.top_breakout_artists(db=db)["notes"])
    assert ar_data.artist_profile("Maple Kid", db=db)["upcoming_shows"] == []
    assert ar_data.snapshot_info(db=db)["days_since_data"] == 12


def test_fresh_snapshot_has_no_stale_note(db):
    assert not any("old" in n for n in ar_data.top_breakout_artists(db=db)["notes"])


def test_shows_score_and_genre_filters(db):
    assert names(ar_data.upcoming_shows(min_breakout_score=95, db=db), "shows", "artist_name") == [
        "Florence Road",
        "CHXRRY",
    ]
    assert names(ar_data.upcoming_shows(genre="indie pop", db=db), "shows", "artist_name") == [
        "CHXRRY",
        "Maple Kid",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [{"max_price": -5}, {"days_ahead": 0}, {"days_ahead": 365}, {"min_breakout_score": 150},
     {"city": "Vancouver"}],
)  # fmt: skip
def test_shows_bad_input_raises_query_error(db, kwargs):
    with pytest.raises(QueryError) as e:
        ar_data.upcoming_shows(**kwargs, db=db)
    assert e.value.hint


# --- safety ---------------------------------------------------------------------------------


def test_injection_shaped_input_is_just_data(db):
    assert ar_data.top_breakout_artists(genre=INJECTION, db=db)["count"] == 0
    assert ar_data.find_artist(INJECTION, db=db)["matched"] is False
    assert ar_data.upcoming_shows(genre=INJECTION, db=db)["count"] == 0
    with pytest.raises(QueryError):
        ar_data.upcoming_shows(city=INJECTION, db=db)
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM artist_signals").fetchall()[0][0] == 9


def test_connection_is_read_only(db):
    with ar_data.connect(db) as con, pytest.raises(duckdb.Error):
        con.execute("DELETE FROM artist_signals")


def test_missing_snapshot_gives_actionable_error(tmp_path):
    with pytest.raises(QueryError) as e:
        ar_data.snapshot_info(db=tmp_path / "nope.duckdb")
    assert "export_snapshot.py" in e.value.hint


def test_snapshot_info(db):
    info = ar_data.snapshot_info(db=db)
    assert info["data_as_of"] == "2026-11-10"
    assert info["source"] == "test"
    assert info["row_counts"] == {
        "artist_growth_history": 20, "artist_signals": 9, "scout_briefs": 1, "upcoming_shows": 7,
        "artist_similar": 6, "event_performers": 8,
    }  # fmt: skip


def test_trend_spreads_a_gap_instead_of_calling_it_a_spike():
    # 5 daily +10s, then a month with no snapshots that gained 300 (also ~10/day).
    days = [dt.date(2026, 10, d) for d in range(14, 19)] + [dt.date(2026, 11, 18)]
    changes = [None, 10, 10, 10, 10, 300]
    listeners = [1000, 1010, 1020, 1030, 1040, 1340]
    points = [
        {"snapshot_date": d.isoformat(), "lastfm_listeners": n, "listeners_change": c}
        for d, n, c in zip(days, listeners, changes, strict=True)
    ]
    trend = ar_data.trend_summary(points)
    assert trend["pattern"] == "steady"
    assert trend["largest_one_day_change"] == 10
    assert trend["gaps"] == [{"from": "2026-10-18", "to": "2026-11-18", "days": 31}]


def test_long_growth_window_gets_a_note(tmp_path):
    tables = fixture_tables()
    tables["artist_signals"]["growth_window_days"] = 31  # first run after a month-long gap
    path = tmp_path / "gap.duckdb"
    write_snapshot(tables, path, "test")
    notes = ar_data.top_breakout_artists(db=path)["notes"]
    assert any("measured over 31 days" in n and "since 2026-10-10" in n for n in notes)


def test_shows_say_who_headlines(db):
    shows = {s["artist_name"]: s for s in ar_data.upcoming_shows(city="Toronto", db=db)["shows"]}
    assert shows["Spoon"]["role"] == "support"
    assert shows["Spoon"]["headliner"] == "Big Star Canada"
    assert shows["Spoon"]["lineup"] == ["Big Star Canada", "Spoon"]
    assert shows["CHXRRY"]["role"] == "headliner" and shows["CHXRRY"]["lineup_size"] == 1
    profile = ar_data.artist_profile("Spoon", db=db)
    assert profile["upcoming_shows"][0]["headliner"] == "Big Star Canada"
