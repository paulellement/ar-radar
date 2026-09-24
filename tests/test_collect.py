import datetime as dt
from collections import Counter

from anr_radar.clients.lastfm import NotFound
from anr_radar.collect import snapshot
from anr_radar.collect.candidates import merge_pool, pool_is_stale
from anr_radar.storage import partition_dir, read_jsonl


def entry(key, name=None):
    return {"artist_key": key, "name": name or key, "mbid": None, "source": "test"}


def test_merge_keeps_existing_and_caps_new():
    existing = [{**entry("old"), "added_date": "2026-09-01", "pool_date": "2026-09-01"}]
    seeds = {"a": entry("a"), "old": entry("old")}
    neighbours = {"n1": entry("n1"), "n2": entry("n2")}
    votes = Counter({"n1": 1, "n2": 5})

    pool = merge_pool(existing, seeds, neighbours, votes, max_size=3, pool_date="2026-09-23")

    keys = [r["artist_key"] for r in pool]
    assert keys == ["old", "a", "n2"]  # existing, then seeds, then most-voted neighbour
    assert pool[0]["added_date"] == "2026-09-01"
    assert all(r["pool_date"] == "2026-09-23" for r in pool)


def test_seed_share_leaves_room_for_neighbours():
    seeds = {f"s{i}": {**entry(f"s{i}"), "source": "geo:Canada"} for i in range(10)}
    neighbours = {f"n{i}": entry(f"n{i}") for i in range(10)}
    pool = merge_pool([], seeds, neighbours, Counter(), max_size=10, pool_date="d", seed_share=0.4)
    keys = [r["artist_key"] for r in pool]
    assert sum(k.startswith("s") for k in keys) == 4
    assert sum(k.startswith("n") for k in keys) == 6


def test_leftover_seeds_fill_spare_room():
    seeds = {f"s{i}": {**entry(f"s{i}"), "source": "tag:jazz"} for i in range(5)}
    pool = merge_pool(
        [], seeds, {"n0": entry("n0")}, Counter(), max_size=10, pool_date="d", seed_share=0.2
    )
    assert len(pool) == 6  # 2 capped seeds + 1 neighbour + 3 leftover seeds


def test_same_name_with_and_without_mbid_is_added_once():
    seeds = {"mbid:123": {**entry("mbid:123", "Björk"), "source": "geo:Iceland"}}
    neighbours = {"name:bjork": entry("name:bjork", "bjork")}
    pool = merge_pool([], seeds, neighbours, Counter(), max_size=10, pool_date="d")
    assert [r["artist_key"] for r in pool] == ["mbid:123"]


def test_existing_members_survive_even_over_cap():
    existing = [{**entry(f"e{i}"), "pool_date": "2026-09-01"} for i in range(5)]
    pool = merge_pool(existing, {"a": entry("a")}, {}, Counter(), max_size=3, pool_date="d")
    assert len(pool) == 5


def test_pool_staleness():
    today = dt.date(2026, 9, 23)
    assert pool_is_stale([], today, 7)
    assert not pool_is_stale([{"pool_date": "2026-09-20"}], today, 7)
    assert pool_is_stale([{"pool_date": "2026-09-16"}], today, 7)


class FakeClient:
    def __init__(self, missing=(), failing=()):
        self.missing, self.failing = set(missing), set(failing)

    def geo_top_artists(self, country, limit=100, page=1):
        return [{"name": "Star", "mbid": "", "listeners": "1000"}]

    def artist_info(self, name):
        if name in self.missing:
            raise NotFound(6, "not found")
        if name in self.failing:
            raise RuntimeError("boom")
        return {"name": name, "stats": {"listeners": "42", "playcount": "100"}}


def write_pool(root, names):
    from anr_radar.collect.candidates import pool_path
    from anr_radar.storage import write_jsonl

    write_jsonl(pool_path(root), [{**entry(n), "pool_date": "2026-09-22"} for n in names])


def test_snapshot_writes_partitions(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot.config, "SEED_COUNTRIES", ["Canada", "Brazil"])
    write_pool(tmp_path, ["a", "b", "gone"])

    result = snapshot.run(FakeClient(missing={"gone"}), str(tmp_path), dt.date(2026, 9, 23))

    assert result["ok"] == 2 and result["not_found"] == 1
    rows = list(
        read_jsonl(
            partition_dir(str(tmp_path), "lastfm/artist_info", "2026-09-23") / "part-0.jsonl"
        )
    )
    assert [r["name"] for r in rows] == ["a", "b"]
    assert rows[0]["payload"]["stats"]["listeners"] == "42"
    geo = list(
        read_jsonl(partition_dir(str(tmp_path), "lastfm/geo_top", "2026-09-23") / "part-0.jsonl")
    )
    assert {g["country"] for g in geo} == {"Canada", "Brazil"}
    assert geo[0]["rank"] == 1


def test_snapshot_fails_loudly_on_high_error_rate(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(snapshot.config, "SEED_COUNTRIES", [])
    write_pool(tmp_path, ["a", "b", "c"])
    with pytest.raises(snapshot.SnapshotIncomplete):
        snapshot.run(FakeClient(failing={"b"}), str(tmp_path), dt.date(2026, 9, 23))
    # rows that did succeed are still written
    out = partition_dir(str(tmp_path), "lastfm/artist_info", "2026-09-23") / "part-0.jsonl"
    assert len(list(read_jsonl(out))) == 2
