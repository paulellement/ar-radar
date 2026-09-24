"""Silver -> gold: breakout signals (scored in pandas by signals.scoring) and growth history.

Gold tables are what Genie and the app read, so they carry full table/column descriptions.
"""

import datetime as dt
import logging
import math

import pandas as pd

from anr_radar.signals.scoring import compute_signals
from anr_radar.spark import set_comments
from anr_radar.tables import Tables

log = logging.getLogger(__name__)

SIGNALS_FIELDS = [
    ("artist_key", "STRING"),
    ("name", "STRING"),
    ("as_of_date", "DATE"),
    ("genres", "STRING"),
    ("tags", "ARRAY<STRING>"),
    ("lastfm_listeners", "BIGINT"),
    ("lastfm_playcount", "BIGINT"),
    ("size_band", "STRING"),
    ("breakout_score", "DOUBLE"),
    ("breakout_rank", "INT"),
    ("breakout_z", "DOUBLE"),
    ("listeners_7d_growth_pct", "DOUBLE"),
    ("listeners_14d_growth_pct", "DOUBLE"),
    ("plays_7d_growth_pct", "DOUBLE"),
    ("new_listeners_7d", "BIGINT"),
    ("new_country_charts_14d", "INT"),
    ("chart_countries", "ARRAY<STRING>"),
    ("listener_growth_z", "DOUBLE"),
    ("play_growth_z", "DOUBLE"),
    ("yt_view_growth_z", "DOUBLE"),
    ("growth_window_days", "INT"),
    ("is_provisional", "BOOLEAN"),
    ("history_days", "INT"),
    ("stats_last_changed", "DATE"),
    ("on_tour", "BOOLEAN"),
    ("lastfm_url", "STRING"),
]
SIGNALS_SCHEMA = ", ".join(f"{name} {type_}" for name, type_ in SIGNALS_FIELDS)
INT_TYPES = {"INT", "BIGINT"}
SCORE_IDX = [name for name, _ in SIGNALS_FIELDS].index("breakout_score")

SIGNALS_COMMENT = (
    "One row per artist for the latest snapshot day: how fast they are growing on Last.fm "
    "relative to artists of a similar size. Use breakout_score (0-100, higher = growing "
    "faster than peers) to find rising artists. A fast-growth signal, not a prediction of hits."
)
SIGNALS_COLUMNS = {
    "artist_key": "Stable artist ID (mbid:<MusicBrainz ID> or name:<normalized name>)",
    "name": "Artist name as shown on Last.fm",
    "as_of_date": "Snapshot date these signals were computed for",
    "genres": "Top 3 Last.fm tags, comma-separated (e.g. 'indie pop, bedroom pop, pop')",
    "tags": "Last.fm tags, most-used first",
    "lastfm_listeners": "All-time unique Last.fm listeners (artist size)",
    "lastfm_playcount": "All-time Last.fm plays",
    "size_band": "Listener size band: <10k, 10k-50k, 50k-500k, 500k-2M, 2M+",
    "breakout_score": (
        "0-100 percentile: how unusual the artist's recent growth is compared with artists of "
        "the same size band. Combines listener growth (45%), play growth (25%), new country "
        "chart entries (15%) and YouTube view growth (15%, when available). Null = no fresh data"
    ),
    "breakout_rank": "1 = highest breakout_score today",
    "breakout_z": "Unscaled weighted score behind breakout_score (robust z-score units)",
    "listeners_7d_growth_pct": "Listener growth over the last 7 days, in percent",
    "listeners_14d_growth_pct": "Listener growth over the last 14 days, in percent",
    "plays_7d_growth_pct": "Play-count growth over the last 7 days, in percent",
    "new_listeners_7d": "New listeners gained over the last 7 days",
    "new_country_charts_14d": "Countries whose Last.fm top-100 the artist entered in 14 days",
    "chart_countries": "Countries whose Last.fm top-100 the artist is in today",
    "listener_growth_z": "Listener growth vs. same-size artists (robust z-score, soft-capped +/-5)",
    "play_growth_z": "Play growth vs. same-size artists (robust z-score, soft-capped +/-5)",
    "yt_view_growth_z": "YouTube view growth vs. same-size artists (null until YouTube is added)",
    "growth_window_days": "Days of history the 7-day growth was actually measured over",
    "is_provisional": "True when growth is measured over fewer than 7 days (early history)",
    "history_days": "Days since the artist was first snapshotted",
    "stats_last_changed": "Last day Last.fm refreshed this artist's stats (they are cached)",
    "on_tour": "Last.fm on-tour flag",
    "lastfm_url": "Artist page on Last.fm",
}


def _clean(value):
    """pandas/numpy scalars -> plain Python, NaN/NaT -> None, for spark.createDataFrame."""
    if isinstance(value, list):
        return value
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return None
    if value is pd.NaT:
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _as_list(value) -> list:
    """Spark arrays arrive from toPandas() as numpy arrays; missing profiles as NaN."""
    if isinstance(value, list):
        return value
    return list(value) if hasattr(value, "__len__") and not isinstance(value, str) else []


def prepare_signals(
    daily: pd.DataFrame, charts: pd.DataFrame, profile: pd.DataFrame
) -> tuple[dt.date, list[tuple]]:
    """Score the latest day and shape rows to SIGNALS_FIELDS. Pure: no Spark."""
    as_of = daily.snapshot_date.max()
    sig = compute_signals(daily, charts, as_of).set_index("artist_key")
    sig = sig.join(profile.set_index("artist_key"), how="left")
    sig["tags"] = sig.tags.apply(_as_list)
    sig["genres"] = sig.tags.apply(lambda v: ", ".join(v[:3]))
    sig = sig.reset_index()

    for name, type_ in SIGNALS_FIELDS:
        if type_ in INT_TYPES:
            sig[name] = sig[name].astype("Int64")  # float NaN columns -> nullable ints
    columns = [name for name, _ in SIGNALS_FIELDS]
    rows = [tuple(_clean(v) for v in rec) for rec in sig[columns].itertuples(index=False)]
    return as_of, rows


def build_signals(spark, t: Tables) -> dt.date:
    daily = spark.sql(f"""
        SELECT artist_key, name, snapshot_date, lastfm_listeners, lastfm_playcount
        FROM {t("silver", "artist_daily")}
    """).toPandas()
    charts = spark.sql(
        f"SELECT artist_key, snapshot_date, country FROM {t('silver', 'chart_daily')}"
    ).toPandas()
    profile = spark.sql(
        f"SELECT artist_key, tags, on_tour, lastfm_url FROM {t('silver', 'artist_profile')}"
    ).toPandas()

    as_of, rows = prepare_signals(daily, charts, profile)
    table = t("gold", "artist_signals")
    (
        spark.createDataFrame(rows, schema=SIGNALS_SCHEMA)
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(table)
    )
    set_comments(spark, table, SIGNALS_COMMENT, SIGNALS_COLUMNS)
    scored = sum(r[SCORE_IDX] is not None for r in rows)
    log.info("gold.artist_signals: %d artists, %d scored, as of %s", len(rows), scored, as_of)
    return as_of


def build_growth_history(spark, t: Tables) -> None:
    table = t("gold", "artist_growth_history")
    spark.sql(f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT artist_key, name, snapshot_date, lastfm_listeners, lastfm_playcount,
               lastfm_listeners - lag(lastfm_listeners) OVER w AS listeners_change,
               lastfm_playcount - lag(lastfm_playcount) OVER w AS playcount_change
        FROM {t("silver", "artist_daily")}
        WINDOW w AS (PARTITION BY artist_key ORDER BY snapshot_date)
    """)
    set_comments(
        spark,
        table,
        "Daily Last.fm listener and play totals per artist, for growth charts and sparklines. "
        "Changes are 0 on days Last.fm did not refresh its cached stats.",
        {
            "snapshot_date": "Snapshot day",
            "listeners_change": "Listener change since the previous snapshot",
            "playcount_change": "Play-count change since the previous snapshot",
        },
    )


def run(spark, t: Tables) -> None:
    build_signals(spark, t)
    build_growth_history(spark, t)
