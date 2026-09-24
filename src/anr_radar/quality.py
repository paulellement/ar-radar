"""Per-layer data quality checks. The pipeline's last task: any failure fails the job,
which emails the alert address."""

import datetime as dt
import logging

from anr_radar.tables import Tables

log = logging.getLogger(__name__)

MIN_COVERAGE = 0.90  # latest day's silver rows vs. candidate pool size
MAX_PARSE_LOSS = 0.01  # bronze rows dropped by silver parsing (beyond name dedupe)


class QualityCheckFailed(RuntimeError):
    pass


def evaluate(m: dict, today: dt.date) -> list[str]:
    """Return human-readable failures for a dict of metrics (see collect_metrics)."""
    failures = []
    oldest_ok = today - dt.timedelta(days=1)
    if m["latest_date"] is None or m["latest_date"] < oldest_ok:
        failures.append(f"stale: newest snapshot is {m['latest_date']}, expected >= {oldest_ok}")
    if m["pool_size"] and m["latest_rows"] < MIN_COVERAGE * m["pool_size"]:
        failures.append(f"low coverage: {m['latest_rows']} rows for a pool of {m['pool_size']}")
    if m["duplicate_rows"]:
        failures.append(f"{m['duplicate_rows']} duplicate (artist_key, snapshot_date) silver rows")
    # Silver keeps one row per distinct name (expected dedupe); anything beyond that was lost.
    parse_loss = m["bronze_latest_distinct_names"] - m["latest_rows"]
    if parse_loss > MAX_PARSE_LOSS * m["bronze_latest_rows"]:
        failures.append(f"silver lost {parse_loss} of {m['bronze_latest_rows']} bronze rows")
    if m["gold_rows"] != m["latest_rows"]:
        failures.append(
            f"gold has {m['gold_rows']} artists, silver's latest day has {m['latest_rows']}"
        )
    if m["gold_bad_scores"]:
        failures.append(f"{m['gold_bad_scores']} breakout scores outside 0-100")
    return failures


def collect_metrics(spark, t: Tables) -> dict:
    daily = t("silver", "artist_daily")
    bronze = t("bronze", "lastfm_artist_info_raw")

    def scalar(sql: str):
        return spark.sql(sql).first()[0]

    latest = scalar(f"SELECT max(snapshot_date) FROM {daily}")
    return {
        "latest_date": latest,
        "pool_size": scalar(f"SELECT count(*) FROM {t('bronze', 'candidate_pool')}"),
        "latest_rows": scalar(f"SELECT count(*) FROM {daily} WHERE snapshot_date = DATE'{latest}'"),
        "duplicate_rows": scalar(f"""
            SELECT count(*) FROM (SELECT artist_key, snapshot_date FROM {daily}
            GROUP BY ALL HAVING count(*) > 1)"""),
        "bronze_latest_rows": scalar(
            f"SELECT count(*) FROM {bronze} WHERE snapshot_date = DATE'{latest}'"
        ),
        "bronze_latest_distinct_names": scalar(f"""
            SELECT count(DISTINCT lower(raw:payload.name::string)) FROM {bronze}
            WHERE snapshot_date = DATE'{latest}'"""),
        "gold_rows": scalar(f"SELECT count(*) FROM {t('gold', 'artist_signals')}"),
        "gold_bad_scores": scalar(f"""
            SELECT count(*) FROM {t("gold", "artist_signals")}
            WHERE breakout_score < 0 OR breakout_score > 100"""),
    }


def run(spark, t: Tables, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.UTC).date()
    metrics = collect_metrics(spark, t)
    log.info("quality metrics: %s", metrics)
    failures = evaluate(metrics, today)
    if failures:
        raise QualityCheckFailed("; ".join(failures))
    log.info("all quality checks passed")
    return metrics
