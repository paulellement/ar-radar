"""Build the candidate artist pool: country + genre seeds, expanded one hop via artist.getSimilar.

Seed charts are dominated by superstars; the similar-artist hop is what reaches the mid-tier
artists that matter for scouting (and for cheap shows).
"""

import argparse
import datetime as dt
import logging
from collections import Counter
from pathlib import Path

from anr_radar import config
from anr_radar.clients.lastfm import LastFmClient, LastFmError
from anr_radar.keys import artist_key, normalize_name
from anr_radar.secrets import get_secret
from anr_radar.storage import partition_dir, read_jsonl, write_jsonl

log = logging.getLogger(__name__)


def pool_path(root: str) -> Path:
    return Path(root) / "candidates" / "pool.jsonl"


def load_pool(root: str) -> list[dict]:
    path = pool_path(root)
    return list(read_jsonl(path)) if path.exists() else []


def pool_is_stale(pool: list[dict], today: dt.date, max_age_days: int) -> bool:
    if not pool:
        return True
    built = dt.date.fromisoformat(max(row["pool_date"] for row in pool))
    return (today - built).days >= max_age_days


def _entry(artist: dict, source: str) -> dict:
    name = artist["name"]
    mbid = artist.get("mbid") or None
    return {"artist_key": artist_key(name, mbid), "name": name, "mbid": mbid, "source": source}


def collect_seeds(client: LastFmClient, countries: list[str], tags: list[str]) -> dict[str, dict]:
    seeds: dict[str, dict] = {}
    for country in countries:
        try:
            artists = client.geo_top_artists(country, limit=config.GEO_LIMIT)
        except LastFmError as exc:
            log.warning("geo seed %s failed: %s", country, exc)
            continue
        for a in artists:
            e = _entry(a, f"geo:{country}")
            seeds.setdefault(e["artist_key"], e)
    for tag in tags:
        for page in range(1, config.TAG_PAGES + 1):
            try:
                artists = client.tag_top_artists(tag, limit=config.TAG_PAGE_SIZE, page=page)
            except LastFmError as exc:
                log.warning("tag seed %s p%d failed: %s", tag, page, exc)
                continue
            for a in artists:
                e = _entry(a, f"tag:{tag}")
                seeds.setdefault(e["artist_key"], e)
    return seeds


def expand_similar(
    client: LastFmClient, seeds: dict[str, dict]
) -> tuple[dict[str, dict], Counter, list[dict]]:
    """Return neighbour entries, how many seeds point at each, and the raw similarity edges."""
    neighbours: dict[str, dict] = {}
    votes: Counter = Counter()
    edges: list[dict] = []
    for i, seed in enumerate(seeds.values(), 1):
        if i % 250 == 0:
            log.info("similar expansion: %d/%d seeds", i, len(seeds))
        try:
            similar = client.similar_artists(seed["name"], limit=config.SIMILAR_LIMIT)
        except LastFmError as exc:
            log.debug("similar for %s failed: %s", seed["name"], exc)
            continue
        for a in similar:
            e = _entry(a, f"similar:{seed['name']}")
            edges.append(
                {
                    "from_key": seed["artist_key"],
                    "to_key": e["artist_key"],
                    "to_name": e["name"],
                    "match": float(a.get("match") or 0),
                }
            )
            if e["artist_key"] not in seeds:
                neighbours.setdefault(e["artist_key"], e)
                votes[e["artist_key"]] += 1
    return neighbours, votes, edges


def _is_seed(entry: dict) -> bool:
    return entry["source"].startswith(("geo:", "tag:"))


def merge_pool(
    existing: list[dict],
    seeds: dict[str, dict],
    neighbours: dict[str, dict],
    votes: Counter,
    max_size: int,
    pool_date: str,
    seed_share: float = config.MAX_SEED_SHARE,
) -> list[dict]:
    """Existing members are always kept (they carry history). New members fill up to max_size:
    seeds up to seed_share of the pool, then neighbours most-recommended first, then any
    leftover seeds. Seeds are mostly superstars; the cap keeps room for mid-tier artists."""
    pool = {row["artist_key"]: {**row, "pool_date": pool_date} for row in existing}
    seed_budget = int(max_size * seed_share) - sum(_is_seed(r) for r in pool.values())
    new_seeds = [e for k, e in seeds.items() if k not in pool]
    new_neighbours = sorted(
        (e for k, e in neighbours.items() if k not in pool),
        key=lambda e: -votes[e["artist_key"]],
    )
    capped = max(seed_budget, 0)
    # Last.fm has one page per name, but the same artist can arrive with and without an mbid.
    names = {normalize_name(r["name"]) for r in pool.values()}
    for e in new_seeds[:capped] + new_neighbours + new_seeds[capped:]:
        if len(pool) >= max_size:
            break
        if normalize_name(e["name"]) in names:
            continue
        names.add(normalize_name(e["name"]))
        pool[e["artist_key"]] = {**e, "added_date": pool_date, "pool_date": pool_date}
    return list(pool.values())


def add_event_performers(
    pool: list[dict], performers: list[dict], pool_date: str, cap: int
) -> tuple[list[dict], int]:
    """Add performers of upcoming local shows that the pool doesn't have yet (by key or name).
    They sit outside MAX_POOL_SIZE, under their own cap."""
    keys = {r["artist_key"] for r in pool}
    names = {normalize_name(r["name"]) for r in pool}
    budget = cap - sum(r["source"].startswith("event:") for r in pool)
    added = []
    for p in performers:
        if len(added) >= budget:
            break
        name = normalize_name(p["name"])
        if p["artist_key"] in keys or name in names:
            continue
        keys.add(p["artist_key"])
        names.add(name)
        added.append({**p, "added_date": pool_date, "pool_date": pool_date})
    return pool + added, len(added)


def save_pool(root: str, pool: list[dict]) -> None:
    write_jsonl(pool_path(root), pool)


def build_pool(client: LastFmClient, root: str, today: dt.date) -> list[dict]:
    existing = load_pool(root)
    seeds = collect_seeds(client, config.SEED_COUNTRIES, config.SEED_TAGS)
    log.info("%d unique seed artists", len(seeds))
    neighbours, votes, edges = expand_similar(client, seeds)
    log.info("%d unique neighbour artists", len(neighbours))

    pool = merge_pool(existing, seeds, neighbours, votes, config.MAX_POOL_SIZE, today.isoformat())
    write_jsonl(pool_path(root), pool)
    write_jsonl(
        partition_dir(root, "candidates/pool_history", today.isoformat()) / "pool.jsonl", pool
    )
    write_jsonl(partition_dir(root, "lastfm/similar", today.isoformat()) / "part-0.jsonl", edges)
    log.info("pool written: %d artists (%d existing)", len(pool), len(existing))
    return pool


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Rebuild the A&R Radar candidate pool")
    parser.add_argument("--root", default=config.DEFAULT_LANDING_ROOT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = LastFmClient(
        get_secret("LASTFM_API_KEY", "lastfm_key"), requests_per_sec=config.LASTFM_REQUESTS_PER_SEC
    )
    build_pool(client, args.root, dt.datetime.now(dt.UTC).date())


if __name__ == "__main__":
    main()
