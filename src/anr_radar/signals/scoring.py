"""Breakout signals from daily snapshots. Pure pandas: no Spark, so it is unit-testable.

Inputs are long-format histories (one row per artist per day). Growth is a per-day log rate
over up to N days of history, so scores exist from day 2; rows with less than
SHORT_WINDOW_DAYS of history are flagged provisional.

Last.fm serves cached artist stats that refresh on a size-dependent schedule (big artists
more often). Identical listeners AND playcount across a window therefore means "not
refreshed yet", not "zero growth", and is treated as a missing measurement.
"""

import datetime as dt

import numpy as np
import pandas as pd

from anr_radar import config

MAD_TO_SD = 1.4826  # scales median absolute deviation to a standard deviation for normal data


def size_band(listeners: pd.Series) -> pd.Series:
    bounds = [b for b, _ in config.SIZE_BANDS] + [np.inf]
    labels = [label for _, label in config.SIZE_BANDS]
    return pd.cut(listeners, bins=bounds, labels=labels, right=False).astype("string")


def window_log_rate(
    history: pd.DataFrame, value_col: str, as_of: dt.date, window_days: int
) -> pd.DataFrame:
    """Per-day log growth of value_col between the latest point on or before as_of - window
    and as_of (or the earliest earlier point, if history is shorter than the window).

    Returns one row per artist observed on as_of: now, then, span_days, rate.
    """
    h = history[["artist_key", "snapshot_date", value_col]].dropna()
    now = h[h.snapshot_date == as_of].set_index("artist_key")[value_col]

    prior = h[h.snapshot_date < as_of].sort_values("snapshot_date")
    target = as_of - dt.timedelta(days=window_days)
    in_window_base = prior[prior.snapshot_date <= target].groupby("artist_key").tail(1)
    earliest = prior.groupby("artist_key").head(1)
    base = in_window_base.set_index("artist_key").combine_first(earliest.set_index("artist_key"))

    out = pd.DataFrame({"now": now})
    out["then"] = base[value_col]
    out["span_days"] = (as_of - base["snapshot_date"]).apply(
        lambda d: d.days if pd.notna(d) else np.nan
    )
    valid = (out.now > 0) & (out.then > 0) & (out.span_days >= 1)
    out["rate"] = np.where(valid, np.log(out.now.where(valid) / out.then.where(valid)), np.nan)
    out["rate"] = out.rate / out.span_days
    return out


def robust_z_by_band(values: pd.Series, bands: pd.Series) -> pd.Series:
    """(x - median) / (1.4826 * MAD) within each size band, soft-clipped to +/- Z_CLIP.
    Bands with too few artists (or zero spread) use pool-wide statistics instead.

    The soft clip (Z_CLIP * tanh(z / Z_CLIP)) bounds outliers so one component can't dominate
    the score, but unlike a hard clip it keeps extreme artists in order instead of tied."""

    def stats(x: pd.Series) -> tuple[float, float]:
        med = x.median()
        return med, MAD_TO_SD * (x - med).abs().median()

    g_med, g_scale = stats(values.dropna())
    z = pd.Series(np.nan, index=values.index)
    for band in bands.dropna().unique():
        members = values[(bands == band) & values.notna()]
        med, scale = stats(members)
        if len(members) < config.MIN_BAND_SIZE or not scale > 0:
            med, scale = g_med, g_scale
        if scale > 0:
            z[members.index] = (members - med) / scale
        else:
            z[members.index] = 0.0
    return config.Z_CLIP * np.tanh(z / config.Z_CLIP)


def new_chart_entries(charts: pd.DataFrame, as_of: dt.date, window_days: int) -> pd.DataFrame:
    """Countries whose top chart the artist entered for the first time within the window.

    The first day of chart data is a baseline, so it never counts as an entry."""
    c = charts[charts.snapshot_date <= as_of]
    if c.empty:
        return pd.DataFrame(columns=["new_country_charts", "chart_countries"])
    baseline = c.snapshot_date.min()
    first_seen = c.groupby(["artist_key", "country"]).snapshot_date.min().reset_index()
    window_start = as_of - dt.timedelta(days=window_days - 1)
    entries = first_seen[
        (first_seen.snapshot_date > baseline) & (first_seen.snapshot_date >= window_start)
    ]
    today = c[c.snapshot_date == as_of].groupby("artist_key").country.apply(sorted)
    return pd.DataFrame(
        {
            "new_country_charts": entries.groupby("artist_key").size(),
            "chart_countries": today,
        }
    )


def combine_components(components: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted mean over the components each artist actually has."""
    w = pd.Series(weights)
    present = components[w.index].notna()
    weighted = components[w.index].fillna(0).mul(w).sum(axis=1)
    total = present.mul(w).sum(axis=1)
    return weighted.div(total.where(total > 0))


def last_change_date(daily: pd.DataFrame, as_of: dt.date) -> pd.Series:
    """Most recent date on which an artist's Last.fm stats differed from the previous snapshot."""
    h = daily[daily.snapshot_date <= as_of].sort_values("snapshot_date")
    cols = ["lastfm_listeners", "lastfm_playcount"]
    changed = h.groupby("artist_key")[cols].diff().ne(0).any(axis=1)
    changed &= h.groupby("artist_key").cumcount() > 0  # first observation isn't a change
    return h[changed].groupby("artist_key").snapshot_date.max()


def possible_alias(name: str, similar_names) -> str | None:
    """A similar artist with the same name once accents/punctuation are stripped is almost
    always the same act under a restyled name (e.g. "JAY-Z" -> "JAŸ-Z"). The new page looks
    like explosive growth, so flag it."""
    from anr_radar.matching.names import normalize

    for other in similar_names if similar_names is not None else []:
        if other != name and normalize(other) == normalize(name):
            return other
    return None


def _pct(rate: pd.Series, days: int) -> pd.Series:
    return (np.exp(rate * days) - 1) * 100


def compute_signals(
    daily: pd.DataFrame,
    charts: pd.DataFrame,
    as_of: dt.date,
    youtube: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per artist observed on as_of.

    daily:   artist_key, snapshot_date, name, lastfm_listeners, lastfm_playcount
    charts:  artist_key, snapshot_date, country
    youtube: artist_key, snapshot_date, yt_views (optional)
    """
    short, long = config.SHORT_WINDOW_DAYS, config.LONG_WINDOW_DAYS
    today = daily[daily.snapshot_date == as_of].set_index("artist_key")
    out = today[["name", "lastfm_listeners", "lastfm_playcount"]].copy()
    out["size_band"] = size_band(out.lastfm_listeners)
    first_seen = daily.groupby("artist_key").snapshot_date.min()
    out["history_days"] = [(as_of - first_seen[k]).days + 1 for k in out.index]

    def aligned(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.reindex(out.index)

    listeners_short = aligned(window_log_rate(daily, "lastfm_listeners", as_of, short))
    listeners_long = aligned(window_log_rate(daily, "lastfm_listeners", as_of, long))
    plays_short = aligned(window_log_rate(daily, "lastfm_playcount", as_of, short))

    # Unrefreshed cache -> no measurement (see module docstring).
    for listeners, plays in ((listeners_short, plays_short), (listeners_long, None)):
        stale = listeners.now == listeners.then
        if plays is not None:
            stale &= plays.now == plays.then
            plays.loc[stale, "rate"] = np.nan
        listeners.loc[stale, "rate"] = np.nan
    out["stats_last_changed"] = last_change_date(daily, as_of).reindex(out.index)
    out["growth_window_days"] = listeners_short.span_days.where(listeners_short.rate.notna())
    out["is_provisional"] = ~(out.growth_window_days >= short)  # growth measured over < 7 days
    out["listeners_7d_growth_pct"] = _pct(listeners_short.rate, short)
    out["listeners_14d_growth_pct"] = _pct(listeners_long.rate, long)
    out["plays_7d_growth_pct"] = _pct(plays_short.rate, short)
    out["new_listeners_7d"] = (
        (listeners_short.now - listeners_short.then) * short / out.growth_window_days
    ).round()

    out["listener_growth_z"] = robust_z_by_band(listeners_short.rate, out.size_band)
    out["play_growth_z"] = robust_z_by_band(plays_short.rate, out.size_band)

    chart = aligned(new_chart_entries(charts, as_of, config.CHART_WINDOW_DAYS))
    out["new_country_charts_14d"] = chart.new_country_charts.fillna(0).astype(int)
    out["chart_countries"] = chart.chart_countries.apply(lambda v: v if isinstance(v, list) else [])
    out["chart_entry_points"] = out.new_country_charts_14d.clip(upper=config.CHART_ENTRY_CAP)

    out["yt_view_growth_z"] = np.nan
    if youtube is not None and not youtube.empty:
        yt = aligned(window_log_rate(youtube, "yt_views", as_of, short))
        out["yt_view_growth_z"] = robust_z_by_band(yt.rate, out.size_band)

    # Only artists with measurable listener growth get a score.
    has_growth = out.listener_growth_z.notna()
    breakout_z = combine_components(out, config.SCORE_WEIGHTS).where(has_growth)
    out["breakout_z"] = breakout_z
    out["breakout_score"] = (breakout_z.rank(pct=True) * 100).round(1)
    out["breakout_rank"] = breakout_z.rank(ascending=False, method="min").astype("Int64")
    out["as_of_date"] = as_of
    return out.reset_index().sort_values("breakout_rank", na_position="last")
