import datetime as dt

import numpy as np
import pandas as pd

from anr_radar.transform.gold import SIGNALS_FIELDS, prepare_signals

PY_TYPES = {
    "STRING": str,
    "DATE": dt.date,
    "BIGINT": int,
    "INT": int,
    "DOUBLE": float,
    "BOOLEAN": bool,
    "ARRAY<STRING>": list,
}


def fixture():
    d0, d1 = dt.date(2026, 9, 23), dt.date(2026, 9, 24)
    daily = pd.DataFrame(
        [
            # a refreshed artist, a stale one (cache not refreshed), and a new one with no history
            ("mbid:a", "Alpha", d0, 20_000, 200_000),
            ("mbid:a", "Alpha", d1, 20_500, 206_000),
            ("name:b", "Beta", d0, 50_000, 500_000),
            ("name:b", "Beta", d1, 50_000, 500_000),
            ("name:c", "Gamma", d1, 9_000, 90_000),
        ],
        columns=["artist_key", "name", "snapshot_date", "lastfm_listeners", "lastfm_playcount"],
    )
    charts = pd.DataFrame(
        [("mbid:a", d0, "Canada"), ("mbid:a", d1, "Canada"), ("mbid:a", d1, "Brazil")],
        columns=["artist_key", "snapshot_date", "country"],
    )
    profile = pd.DataFrame(
        [
            # toPandas() returns Spark arrays as numpy arrays
            ("mbid:a", np.array(["indie pop", "bedroom pop", "pop", "rock"]), True, "u/a"),
            ("name:b", np.array([]), False, "u/b"),
        ],
        columns=["artist_key", "tags", "on_tour", "lastfm_url"],
    )  # name:c has no profile
    return daily, charts, profile


def test_rows_match_spark_schema_types():
    as_of, rows = prepare_signals(*fixture())
    assert as_of == dt.date(2026, 9, 24)
    assert len(rows) == 3
    for row in rows:
        assert len(row) == len(SIGNALS_FIELDS)
        for value, (name, type_) in zip(row, SIGNALS_FIELDS, strict=True):
            if value is not None:
                assert isinstance(value, PY_TYPES[type_]), (name, value, type(value))


def test_row_contents():
    _, rows = prepare_signals(*fixture())
    names = [name for name, _ in SIGNALS_FIELDS]
    by_key = {r[0]: dict(zip(names, r, strict=True)) for r in rows}

    a = by_key["mbid:a"]
    assert a["genres"] == "indie pop, bedroom pop, pop"
    assert a["chart_countries"] == ["Brazil", "Canada"]
    assert a["new_country_charts_14d"] == 1  # Brazil; Canada was there on the baseline day
    assert a["breakout_score"] is not None and a["is_provisional"] is True
    assert a["stats_last_changed"] == dt.date(2026, 9, 24)

    assert by_key["name:b"]["breakout_score"] is None  # stale cache: no measurement
    assert by_key["name:b"]["tags"] == []
    assert by_key["name:c"]["tags"] == [] and by_key["name:c"]["lastfm_url"] is None
