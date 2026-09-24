"""Daily Last.fm snapshot: artist.getInfo for every pool artist, plus per-country top charts.

Last.fm only exposes current totals, so these daily files ARE the history that growth
signals are computed from. Output is raw JSONL in the landing Volume, one partition per day.
"""

import argparse
import datetime as dt
import logging

from anr_radar import config
from anr_radar.clients.lastfm import LastFmClient, LastFmError, NotFound
from anr_radar.collect.candidates import build_pool, load_pool, pool_is_stale
from anr_radar.keys import artist_key
from anr_radar.secrets import get_secret
from anr_radar.storage import partition_dir, write_jsonl

log = logging.getLogger(__name__)

MAX_ERROR_RATE = 0.10


class SnapshotIncomplete(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def snapshot_geo_charts(
    client: LastFmClient, countries: list[str], snapshot_date: str
) -> list[dict]:
    rows = []
    for country in countries:
        try:
            artists = client.geo_top_artists(country, limit=config.GEO_LIMIT)
        except LastFmError as exc:
            log.warning("geo chart %s failed: %s", country, exc)
            continue
        fetched_at = _now()
        for rank, a in enumerate(artists, 1):
            rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "fetched_at": fetched_at,
                    "country": country,
                    "rank": rank,
                    "artist_key": artist_key(a["name"], a.get("mbid") or None),
                    "name": a["name"],
                    "mbid": a.get("mbid") or None,
                    "listeners": int(a["listeners"]) if a.get("listeners") else None,
                }
            )
    return rows


def snapshot_artists(client: LastFmClient, pool: list[dict], snapshot_date: str):
    """Return a lazy generator of raw rows and a stats dict that fills in as it is consumed."""
    stats = {"ok": 0, "not_found": 0, "errors": 0}

    def rows():
        for i, member in enumerate(pool, 1):
            if i % 500 == 0:
                log.info("artist.getInfo %d/%d (%s)", i, len(pool), stats)
            try:
                payload = client.artist_info(member["name"])
            except NotFound:
                stats["not_found"] += 1
                continue
            except Exception as exc:  # keep going; the error rate is checked at the end
                log.warning("getInfo %s failed: %s", member["name"], exc)
                stats["errors"] += 1
                continue
            stats["ok"] += 1
            yield {
                "source": "lastfm.artist.getinfo",
                "snapshot_date": snapshot_date,
                "fetched_at": _now(),
                "artist_key": member["artist_key"],
                "name": member["name"],
                "payload": payload,
            }

    return rows(), stats


def run(
    client: LastFmClient,
    root: str,
    today: dt.date,
    limit: int | None = None,
    rebuild_pool: bool = False,
) -> dict:
    snapshot_date = today.isoformat()
    pool = load_pool(root)
    if rebuild_pool or pool_is_stale(pool, today, config.POOL_MAX_AGE_DAYS):
        log.info("candidate pool missing or stale; rebuilding")
        pool = build_pool(client, root, today)
    if limit:
        pool = pool[:limit]

    charts = snapshot_geo_charts(client, config.SEED_COUNTRIES, snapshot_date)
    n_chart = write_jsonl(
        partition_dir(root, "lastfm/geo_top", snapshot_date) / "part-0.jsonl", charts
    )
    log.info("wrote %d geo chart rows", n_chart)

    rows, stats = snapshot_artists(client, pool, snapshot_date)
    n = write_jsonl(partition_dir(root, "lastfm/artist_info", snapshot_date) / "part-0.jsonl", rows)
    log.info("wrote %d artist rows; %s", n, stats)

    if pool and stats["errors"] / len(pool) > MAX_ERROR_RATE:
        raise SnapshotIncomplete(f"too many failed lookups: {stats}")
    return {"pool": len(pool), "geo_rows": n_chart, **stats}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Take today's Last.fm snapshot")
    parser.add_argument("--root", default=config.DEFAULT_LANDING_ROOT)
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date()
    )
    parser.add_argument("--limit", type=int, help="only snapshot the first N artists (smoke test)")
    parser.add_argument("--rebuild-pool", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = LastFmClient(
        get_secret("LASTFM_API_KEY", "lastfm_key"), requests_per_sec=config.LASTFM_REQUESTS_PER_SEC
    )
    run(client, args.root, args.date, limit=args.limit, rebuild_pool=args.rebuild_pool)


if __name__ == "__main__":
    main()
