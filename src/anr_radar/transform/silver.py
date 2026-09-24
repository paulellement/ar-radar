"""Bronze -> silver: parse the raw VARIANT payloads into typed, deduplicated tables.

Tables are rebuilt in full each run (CREATE OR REPLACE). At ~5k artists/day this is cheap and
keeps the logic simple; switch to MERGE if the pool or history grows by orders of magnitude.
"""

import logging

from anr_radar.tables import Tables

log = logging.getLogger(__name__)


def _string_array(expr: str, field: str) -> str:
    """Last.fm returns a list of objects, a single bare object, or "" for these fields."""
    return f"""coalesce(
        transform(try_variant_get({expr}, '$', 'array<variant>'),
                  x -> variant_get(x, '$.{field}', 'string')),
        CASE WHEN variant_get({expr}, '$.{field}', 'string') IS NOT NULL
             THEN array(variant_get({expr}, '$.{field}', 'string')) END,
        CAST(array() AS ARRAY<STRING>)
    )"""


def statements(t: Tables) -> list[str]:
    info_parsed = f"""
    CREATE OR REPLACE TEMP VIEW info_parsed AS
    SELECT
      snapshot_date,
      raw:artist_key::string                      AS artist_key,
      raw:payload.name::string                    AS name,
      nullif(raw:payload.mbid::string, '')        AS mbid,
      try_cast(raw:payload.stats.listeners::string AS BIGINT) AS lastfm_listeners,
      try_cast(raw:payload.stats.playcount::string AS BIGINT) AS lastfm_playcount,
      {_string_array("raw:payload.tags.tag", "name")} AS tags,
      raw:payload.ontour::string = '1'            AS on_tour,
      raw:payload.url::string                     AS lastfm_url,
      raw:payload.bio.summary::string             AS bio_summary_html,
      {_string_array("raw:payload.similar.artist", "name")} AS similar_names,
      raw:fetched_at::timestamp                   AS fetched_at
    FROM {t("bronze", "lastfm_artist_info_raw")}
    """

    # Last.fm has one page per name, but the pool can hold the same artist under an mbid key
    # and a name key. Keep one row per (name, day), preferring the mbid key.
    artist_daily = f"""
    CREATE OR REPLACE TABLE {t("silver", "artist_daily")}
    COMMENT 'One row per artist per snapshot day from Last.fm artist.getInfo. Listeners and playcount are cumulative all-time totals, cached by Last.fm and refreshed on a size-dependent schedule.'
    AS SELECT artist_key, name, mbid, snapshot_date, lastfm_listeners, lastfm_playcount, tags, on_tour
    FROM info_parsed
    WHERE lastfm_listeners IS NOT NULL
    QUALIFY row_number() OVER (
      PARTITION BY lower(name), snapshot_date
      ORDER BY (artist_key LIKE 'mbid:%') DESC, artist_key, fetched_at DESC) = 1
    """

    artist_profile = rf"""
    CREATE OR REPLACE TABLE {t("silver", "artist_profile")}
    COMMENT 'Latest descriptive info per artist: tags, bio summary, Last.fm URL, similar artists.'
    AS SELECT
      p.artist_key, p.name, p.mbid, p.tags, p.on_tour, p.lastfm_url,
      trim(regexp_replace(p.bio_summary_html, '<a href=.*?</a>\\.?', '')) AS bio_summary,
      p.similar_names,
      p.snapshot_date AS profile_date
    FROM info_parsed p
    JOIN (SELECT artist_key, max(snapshot_date) AS d
          FROM {t("silver", "artist_daily")} GROUP BY artist_key) latest
      ON p.artist_key = latest.artist_key AND p.snapshot_date = latest.d
    QUALIFY row_number() OVER (PARTITION BY p.artist_key ORDER BY p.fetched_at DESC) = 1
    """

    # Chart rows carry their own key (mbid or name); resolve to the pool's key by name so a
    # chart entry joins to the artist's history.
    chart_daily = f"""
    CREATE OR REPLACE TABLE {t("silver", "chart_daily")}
    COMMENT 'Daily Last.fm top-100 artists per country (geo.getTopArtists). Used for new-country-chart signals.'
    AS WITH charts AS (
      SELECT
        snapshot_date,
        raw:country::string        AS country,
        raw:rank::int              AS rank,
        raw:artist_key::string     AS chart_artist_key,
        raw:name::string           AS name,
        raw:listeners::bigint      AS country_listeners
      FROM {t("bronze", "lastfm_geo_top_raw")}
      QUALIFY row_number() OVER (
        PARTITION BY snapshot_date, raw:country::string, raw:rank::int
        ORDER BY raw:fetched_at::string DESC) = 1
    ),
    names AS (
      SELECT lower(name) AS lname, artist_key FROM {t("silver", "artist_profile")}
      QUALIFY row_number() OVER (PARTITION BY lower(name) ORDER BY artist_key) = 1
    )
    SELECT c.snapshot_date, c.country, c.rank,
           coalesce(n.artist_key, c.chart_artist_key) AS artist_key,
           n.artist_key IS NOT NULL AS in_pool,
           c.name, c.country_listeners
    FROM charts c LEFT JOIN names n ON lower(c.name) = n.lname
    """

    artist_similar = f"""
    CREATE OR REPLACE TABLE {t("silver", "artist_similar")}
    COMMENT 'Last.fm artist.getSimilar edges from the most recent candidate-pool build. match is 0-1.'
    AS SELECT
      snapshot_date,
      raw:from_key::string AS from_key,
      raw:to_key::string   AS to_key,
      raw:to_name::string  AS to_name,
      raw:match::double    AS match
    FROM {t("bronze", "lastfm_similar_raw")}
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM {t("bronze", "lastfm_similar_raw")})
    """
    return [info_parsed, artist_daily, artist_profile, chart_daily, artist_similar]


def run(spark, t: Tables) -> None:
    for sql in statements(t):
        spark.sql(sql)
    counts = spark.sql(
        f"SELECT count(*), count(DISTINCT snapshot_date) FROM {t('silver', 'artist_daily')}"
    ).first()
    log.info("silver.artist_daily: %d rows over %d days", counts[0], counts[1])
