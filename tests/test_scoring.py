import datetime as dt

import numpy as np
import pandas as pd
import pytest

from anr_radar import config
from anr_radar.signals import scoring

AS_OF = dt.date(2026, 10, 10)


def day(n: int) -> dt.date:
    return AS_OF - dt.timedelta(days=n)


def history(artists: dict[str, list[tuple[int, float, float]]]) -> pd.DataFrame:
    """artists: key -> [(days_ago, listeners, playcount), ...]"""
    rows = [
        {
            "artist_key": k,
            "name": k,
            "snapshot_date": day(d),
            "lastfm_listeners": lis,
            "lastfm_playcount": plays,
        }
        for k, points in artists.items()
        for d, lis, plays in points
    ]
    return pd.DataFrame(rows)


NO_CHARTS = pd.DataFrame(columns=["artist_key", "snapshot_date", "country"])


def band_population(
    prefix: str, base: float, weekly_growth: float, n: int = 12, noise: float = 0.02
) -> dict:
    """n similar-size artists growing ~weekly_growth per week, +/- noise (absolute)."""
    rng = np.random.default_rng(len(prefix))
    out = {}
    for i in range(n):
        g = weekly_growth + rng.uniform(-noise, noise)
        size = base * (1 + i / (4 * n))
        out[f"{prefix}{i}"] = [(7, size, size * 10), (0, size * (1 + g), size * 10 * (1 + g))]
    return out


def test_size_band_edges():
    s = scoring.size_band(pd.Series([0, 9_999, 10_000, 499_999, 500_000, 5_000_000]))
    assert list(s) == ["<10k", "<10k", "10k-50k", "50k-500k", "500k-2M", "2M+"]


def test_small_artist_jump_beats_big_artist_same_relative_backdrop():
    # Both bands grow ~1%/week; 20k->26k is a far bigger outlier than 2M->2.1M.
    pop = band_population("s", 20_000, 0.01) | band_population("b", 2_500_000, 0.01)
    pop["small"] = [(7, 20_000, 200_000), (0, 26_000, 260_000)]
    pop["big"] = [(7, 2_000_000, 2e7), (0, 2_100_000, 2.1e7)]
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF).set_index("artist_key")
    assert sig.loc["small", "breakout_rank"] < sig.loc["big", "breakout_rank"]
    assert sig.loc["small", "breakout_rank"] == 1


def test_growth_is_judged_against_same_size_artists():
    # Small artists typically grow 20%/week, big ones 1%/week. A big artist at 5% is a
    # bigger anomaly than a small one at 22%, even though 22% > 5% in raw terms.
    pop = band_population("s", 20_000, 0.20) | band_population("b", 2_500_000, 0.01)
    pop["small"] = [(7, 20_000, 200_000), (0, 24_400, 244_000)]
    pop["big"] = [(7, 2_600_000, 2.6e7), (0, 2_730_000, 2.73e7)]
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF).set_index("artist_key")
    assert sig.loc["big", "listener_growth_z"] > sig.loc["small", "listener_growth_z"]


def test_short_history_scores_but_is_provisional():
    pop = band_population("s", 20_000, 0.05)
    pop["new"] = [(1, 30_000, 300_000), (0, 30_300, 303_000)]
    pop["today_only"] = [(0, 30_000, 300_000)]
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF).set_index("artist_key")

    assert sig.loc["new", "growth_window_days"] == 1
    assert sig.loc["new", "is_provisional"]
    assert sig.loc["new", "listeners_7d_growth_pct"] == pytest.approx((1.01**7 - 1) * 100)
    assert sig.loc["new", "new_listeners_7d"] == 2_100
    assert not sig.loc["s0", "is_provisional"]
    assert pd.isna(sig.loc["today_only", "breakout_score"])


def test_uses_latest_point_on_or_before_window_start():
    # Gap in history: points at 10, 8 and 0 days ago -> the 7-day baseline is 8 days ago.
    h = history({"a": [(10, 100, 1000), (8, 200, 2000), (0, 400, 4000)]})
    r = scoring.window_log_rate(h, "lastfm_listeners", AS_OF, 7).loc["a"]
    assert r.span_days == 8
    assert r.rate == pytest.approx(np.log(2) / 8)


def test_declines_and_zeros_do_not_crash():
    pop = band_population("s", 20_000, 0.05)
    pop["shrinking"] = [(7, 20_000, 200_000), (0, 19_000, 190_000)]  # Last.fm corrections
    pop["zero"] = [(7, 0, 0), (0, 50, 60)]
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF).set_index("artist_key")
    assert sig.loc["shrinking", "listeners_7d_growth_pct"] < 0
    assert sig.loc["shrinking", "breakout_rank"] == sig.breakout_rank.max()
    assert pd.isna(sig.loc["zero", "breakout_score"])


def test_missing_today_is_excluded():
    h = history({"gone": [(7, 100, 1000), (1, 120, 1200)]})
    assert scoring.compute_signals(h, NO_CHARTS, AS_OF).empty


def test_new_chart_entries_ignore_baseline_and_old_entries():
    charts = pd.DataFrame(
        [
            # baseline day: already charting in Canada -> not an entry
            {"artist_key": "a", "snapshot_date": day(30), "country": "Canada"},
            {"artist_key": "a", "snapshot_date": AS_OF, "country": "Canada"},
            # entered Brazil 20 days ago -> outside the 14-day window
            {"artist_key": "a", "snapshot_date": day(20), "country": "Brazil"},
            # entered Mexico and Chile within the window
            {"artist_key": "a", "snapshot_date": day(3), "country": "Mexico"},
            {"artist_key": "a", "snapshot_date": AS_OF, "country": "Chile"},
        ]
    )
    out = scoring.new_chart_entries(charts, AS_OF, 14)
    assert out.loc["a", "new_country_charts"] == 2
    assert out.loc["a", "chart_countries"] == ["Canada", "Chile"]


def test_missing_components_renormalise_weights():
    comps = pd.DataFrame(
        {
            "listener_growth_z": [2.0],
            "play_growth_z": [1.0],
            "chart_entry_points": [0.0],
            "yt_view_growth_z": [np.nan],
        }
    )
    w = config.SCORE_WEIGHTS
    expected = (2.0 * w["listener_growth_z"] + 1.0 * w["play_growth_z"]) / (
        w["listener_growth_z"] + w["play_growth_z"] + w["chart_entry_points"]
    )
    assert scoring.combine_components(comps, w).iloc[0] == pytest.approx(expected)


def test_score_is_percentile_between_0_and_100():
    pop = band_population("s", 20_000, 0.05, n=20)
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF)
    assert sig.breakout_score.between(0, 100).all()
    assert sig.breakout_score.max() == 100
    assert list(sig.breakout_rank) == sorted(sig.breakout_rank)


def test_unrefreshed_stats_are_missing_not_zero_growth():
    pop = band_population("s", 20_000, 0.05)
    pop["stale"] = [(7, 30_000, 300_000), (0, 30_000, 300_000)]
    pop["listeners_flat_plays_up"] = [(7, 30_000, 300_000), (0, 30_000, 309_000)]
    sig = scoring.compute_signals(history(pop), NO_CHARTS, AS_OF).set_index("artist_key")

    assert pd.isna(sig.loc["stale", "breakout_score"])
    assert pd.isna(sig.loc["stale", "listeners_7d_growth_pct"])
    assert pd.isna(sig.loc["stale", "stats_last_changed"])
    # Listeners flat but plays moved: the cache did refresh, so 0% listener growth is real.
    assert sig.loc["listeners_flat_plays_up", "listeners_7d_growth_pct"] == 0
    assert sig.loc["listeners_flat_plays_up", "stats_last_changed"] == AS_OF


def test_last_change_date_skips_unchanged_days():
    h = history({"a": [(5, 100, 1000), (4, 110, 1100), (3, 110, 1100), (0, 110, 1100)]})
    assert scoring.last_change_date(h, AS_OF)["a"] == day(4)


@pytest.mark.parametrize(
    ("name", "similar", "expected"),
    [
        ("JAŸ-Z", ["JAY-Z", "Nas"], "JAY-Z"),
        ("Beyonce", ["Beyoncé"], "Beyoncé"),
        ("Jane Remover", ["Jane Doe", "Dltzk"], None),
        ("X", None, None),
    ],
)
def test_possible_alias(name, similar, expected):
    assert scoring.possible_alias(name, similar) == expected
