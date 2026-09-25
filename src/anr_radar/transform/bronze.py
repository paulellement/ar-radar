"""Landing JSONL -> bronze Delta tables. Each raw line is kept whole in a VARIANT column so
API shape changes never break ingestion; parsing happens in silver.

Idempotent per day: a snapshot date is (re)loaded with replaceWhere, so re-running a
collector for a day and then re-running bronze replaces that day rather than duplicating it.
"""

import datetime as dt
import logging
import os

from anr_radar.tables import LAYERS, Tables

log = logging.getLogger(__name__)

# bronze table -> landing dataset folder (partitioned dt=YYYY-MM-DD)
DATASETS = {
    "lastfm_artist_info_raw": "lastfm/artist_info",
    "lastfm_geo_top_raw": "lastfm/geo_top",
    "lastfm_similar_raw": "lastfm/similar",
    "tm_events_raw": "ticketmaster/events",
}
RELOAD_RECENT_DAYS = 2  # always reload the newest partitions in case a day was re-collected


def landed_dates(landing_root: str, dataset: str) -> list[str]:
    path = os.path.join(landing_root, dataset)
    if not os.path.isdir(path):
        return []
    return sorted(d.removeprefix("dt=") for d in os.listdir(path) if d.startswith("dt="))


def dates_to_load(landed: list[str], loaded: set[str], reload_recent: int) -> list[str]:
    recent = set(landed[-reload_recent:]) if reload_recent else set()
    return [d for d in landed if d not in loaded or d in recent]


def ingest_dataset(spark, table: str, landing_root: str, dataset: str) -> list[str]:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {table} (
          snapshot_date DATE, raw VARIANT, _source_file STRING, _ingested_at TIMESTAMP
        ) COMMENT 'Raw JSONL lines from {landing_root}/{dataset}, one row per line'
    """)
    loaded = {str(r[0]) for r in spark.sql(f"SELECT DISTINCT snapshot_date FROM {table}").collect()}
    todo = dates_to_load(landed_dates(landing_root, dataset), loaded, RELOAD_RECENT_DAYS)
    for d in todo:
        dt.date.fromisoformat(d)  # validate before it goes into SQL
        (
            spark.read.text(f"{landing_root}/{dataset}/dt={d}/")
            .selectExpr(
                f"DATE'{d}' AS snapshot_date",
                "parse_json(value) AS raw",
                "_metadata.file_path AS _source_file",
                "current_timestamp() AS _ingested_at",
            )
            .write.mode("overwrite")
            .option("replaceWhere", f"snapshot_date = DATE'{d}'")
            .saveAsTable(table)
        )
    log.info("%s: loaded %s", table, todo or "nothing new")
    return todo


def ingest_pool(spark, table: str, landing_root: str) -> None:
    """The current candidate pool (a single file, replaced weekly)."""
    (
        spark.read.text(f"{landing_root}/candidates/pool.jsonl")
        .selectExpr("parse_json(value) AS raw", "current_timestamp() AS _ingested_at")
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(table)
    )


def run(spark, t: Tables, landing_root: str) -> None:
    for layer in LAYERS:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {t.schema(layer)}")
    for table, dataset in DATASETS.items():
        ingest_dataset(spark, t("bronze", table), landing_root, dataset)
    ingest_pool(spark, t("bronze", "candidate_pool"), landing_root)
