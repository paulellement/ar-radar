"""A tiny, hand-made snapshot (9 artists, 6 shows, one brief), built through the real
export writer, so tests never depend on the real data."""

import datetime as dt

import pandas as pd
import pytest

from export_snapshot import COLUMNS, write_snapshot

AS_OF = dt.date(2026, 11, 10)


def _signals() -> pd.DataFrame:
    # name, key, listeners, band, score, tags, chart_countries, provisional, alias
    artists = [
        (
            "Phoebe Bridgers",
            "mbid:pb",
            1_500_000,
            "500k-2M",
            95.0,
            ["indie folk", "Indie Pop"],
            ["United States"],
            False,
            None,
        ),  # noqa: E501
        (
            "Florence Road",
            "mbid:fr",
            30_000,
            "10k-50k",
            99.0,
            ["indie rock", "irish"],
            ["Ireland"],
            False,
            None,
        ),  # noqa: E501
        (
            "CHXRRY",
            "mbid:cx",
            80_000,
            "50k-500k",
            97.0,
            ["indie pop", "bedroom pop"],
            [],
            False,
            None,
        ),  # noqa: E501
        ("Spoon", "mbid:sp", 900_000, "500k-2M", 60.0, ["indie rock"], [], False, None),
        ("Spoons", "name:spoons", 5_000, "<10k", 40.0, ["new wave", "canadian"], [], False, None),  # noqa: E501
        (
            "Big Star Canada",
            "mbid:bs",
            5_000_000,
            "2M+",
            98.0,
            ["pop", "canadian"],
            ["Canada"],
            False,
            None,
        ),  # noqa: E501
        ("JAŸ-Z", "name:jayz", 8_000, "<10k", 100.0, ["hip-hop"], [], False, "JAY-Z"),
        (
            "Maple Kid",
            "name:maple kid",
            12_000,
            "10k-50k",
            92.0,
            ["indie pop", "canadian indie"],
            ["Canada"],
            True,
            None,
        ),  # noqa: E501
        ("No Data Yet", "name:no data yet", 20_000, "10k-50k", None, ["folk"], [], True, None),
    ]
    rows = []
    for name, key, listeners, band, score, tags, charts, prov, alias in artists:
        rows.append(
            {
                "artist_key": key, "name": name, "as_of_date": AS_OF,
                "genres": ", ".join(tags[:3]), "tags": tags, "lastfm_listeners": listeners,
                "lastfm_playcount": listeners * 20, "size_band": band, "breakout_score": score,
                "breakout_rank": None, "listeners_7d_growth_pct": 5.0 if score else None,
                "listeners_14d_growth_pct": 9.0 if score else None,
                "plays_7d_growth_pct": 6.0 if score else None, "new_listeners_7d": 100,
                "new_country_charts_14d": len(charts), "chart_countries": charts,
                "listener_growth_z": 1.0, "play_growth_z": 1.0, "is_provisional": prov,
                "history_days": 5 if prov else 30, "growth_window_days": 5 if prov else 7,
                "stats_last_changed": AS_OF, "on_tour": False,
                "lastfm_url": f"https://last.fm/music/{name}", "possible_alias_of": alias,
            }
        )  # fmt: skip
    df = pd.DataFrame(rows)
    df["breakout_rank"] = df.breakout_score.rank(ascending=False, method="min").astype("Int64")
    return df[COLUMNS["artist_signals"]]


def _history() -> pd.DataFrame:
    rows = []
    # Maple Kid: +100 a day for 10 days (steady). CHXRRY: flat, then one +5,000 jump.
    for i in range(10):
        day = AS_OF - dt.timedelta(days=9 - i)
        rows.append(("name:maple kid", "Maple Kid", day, 11_000 + 100 * i, 100 if i else 50))
        jump = 5_000 if i == 6 else 0
        listeners = 75_000 + (5_000 if i >= 6 else 0)
        rows.append(("mbid:cx", "CHXRRY", day, listeners, jump))
    df = pd.DataFrame(
        rows,
        columns=["artist_key", "name", "snapshot_date", "lastfm_listeners", "listeners_change"],
    )
    df["lastfm_playcount"] = df.lastfm_listeners * 20
    df["playcount_change"] = df.listeners_change * 20
    return df[COLUMNS["artist_growth_history"]]


def _shows() -> pd.DataFrame:
    def show(days, city, venue, key, name, score, band, price, headliner=True):
        return {
            "event_date": AS_OF + dt.timedelta(days=days), "local_time": "20:00:00",
            "city": city, "venue": venue, "event_name": f"{name} live", "event_id": f"ev-{key}-{days}",  # noqa: E501
            "artist_key": key, "artist_name": name, "is_headliner": headliner,
            "breakout_score": score, "breakout_rank": None, "size_band": band,
            "lastfm_listeners": 1000, "genres": "", "listeners_7d_growth_pct": 5.0,
            "is_provisional": False, "min_price": price, "max_price": price,
            "currency": "CAD" if price else None, "ticket_url": "https://tm.example/x",
            "tm_genre": "Rock", "status": "onsale", "match_method": "mbid",
        }  # fmt: skip

    return pd.DataFrame(
        [
            show(
                5,
                "Toronto",
                "Horseshoe Tavern",
                "name:maple kid",
                "Maple Kid",
                92.0,
                "10k-50k",
                25.0,
            ),  # noqa: E501
            show(10, "Toronto", "Lee's Palace", "mbid:cx", "CHXRRY", 97.0, "50k-500k", None),
            show(
                10,
                "Toronto",
                "Massey Hall",
                "mbid:sp",
                "Spoon",
                60.0,
                "500k-2M",
                60.0,
                headliner=False,
            ),  # noqa: E501
            show(40, "Montreal", "Petit Campus", "mbid:cx", "CHXRRY", 97.0, "50k-500k", None),
            show(
                15, "Montréal", "Bar le Ritz PDB", "mbid:fr", "Florence Road", 99.0, "10k-50k", 20.0
            ),  # noqa: E501
            show(
                80,
                "Toronto",
                "Budweiser Stage",
                "mbid:pb",
                "Phoebe Bridgers",
                95.0,
                "500k-2M",
                80.0,
            ),  # noqa: E501
            show(-9, "Toronto", "The Baby G", "name:maple kid", "Maple Kid", 92.0, "10k-50k", 15.0),  # noqa: E501
        ]
    )[COLUMNS["upcoming_shows"]]


def _briefs() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "artist_key": "mbid:cx", "name": "CHXRRY", "as_of_date": AS_OF,
                "brief_md": "### Who they are\nBedroom pop.\n### Signing status\nXO Records.",
                "signing_status": "XO Records.", "label_mentioned": True,
                "model": "databricks-gpt-oss-120b", "prompt_version": "v2",
                "generated_at": dt.datetime(2026, 11, 9, 6, 0, tzinfo=dt.UTC),  # tz-aware
            }
        ]
    )  # fmt: skip


def _similar() -> pd.DataFrame:
    # Last.fm "similar" edges from the pool build (from_key -> to_key, match 0-1).
    edges = [
        ("mbid:pb", "mbid:cx", "CHXRRY", 0.8),
        ("mbid:pb", "name:not tracked", "Not Tracked", 0.95),  # target outside the pool
        ("mbid:pb", "name:maple kid", "Maple Kid", 0.3),
        ("mbid:fr", "mbid:pb", "Phoebe Bridgers", 0.6),  # only the reverse direction exists
        ("mbid:sp", "name:spoons", "Spoons", 0.9),
        ("name:jayz", "mbid:pb", "Phoebe Bridgers", 0.9),  # an alias page: never recommended
    ]
    return pd.DataFrame(edges, columns=COLUMNS["artist_similar"])


def _performers(shows: pd.DataFrame) -> pd.DataFrame:
    """Each show's bill. Spoon's Massey Hall date is an opening slot for Big Star Canada."""
    rows = []
    for event_id, name, headliner in zip(
        shows.event_id, shows.artist_name, shows.is_headliner, strict=True
    ):  # noqa: E501
        bill = [name] if headliner else ["Big Star Canada", name]
        rows += [(event_id, i, n) for i, n in enumerate(bill)]
    return pd.DataFrame(rows, columns=COLUMNS["event_performers"])


def fixture_tables() -> dict:
    return {
        "artist_signals": _signals(),
        "artist_growth_history": _history(),
        "upcoming_shows": _shows(),
        "scout_briefs": _briefs(),
        "artist_similar": _similar(),
        "event_performers": _performers(_shows()),
    }


@pytest.fixture(autouse=True)
def pin_today(monkeypatch):
    """Tests run as if today were the snapshot date; stale-data tests move it forward."""
    import ar_data

    monkeypatch.setattr(ar_data, "_today", lambda: AS_OF)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ar_radar.duckdb"
    write_snapshot(fixture_tables(), path, source="test")
    return path
