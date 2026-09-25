import datetime as dt

import pytest

from anr_radar.clients import ticketmaster
from anr_radar.clients.ticketmaster import TicketmasterClient
from anr_radar.collect import events
from anr_radar.collect.candidates import add_event_performers

T0 = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)


class FakeTM(TicketmasterClient):
    """Serves a fixed list of event dates; honours window, paging and the 1000-result cap."""

    def __init__(self, event_times):
        super().__init__("k", requests_per_sec=1e6)
        self.event_times = event_times
        self.calls = 0

    def _page(self, city, country, start, end, page):
        self.calls += 1
        hits = [t for t in self.event_times if start <= t < end]
        size = ticketmaster.PAGE_SIZE
        chunk = hits[page * size : (page + 1) * size] if (page + 1) * size <= 1000 else []
        return {
            "page": {"totalElements": len(hits), "totalPages": -(-len(hits) // size)},
            "_embedded": {"events": [{"id": t.isoformat()} for t in chunk]},
        }


def test_small_window_is_paged():
    times = [T0 + dt.timedelta(hours=i) for i in range(450)]
    got = FakeTM(times).music_events("Toronto", "CA", T0, T0 + dt.timedelta(days=90))
    assert len(got) == 450


def test_windows_over_the_deep_paging_limit_are_split():
    times = [T0 + dt.timedelta(minutes=90 * i) for i in range(2500)]
    client = FakeTM(times)
    got = client.music_events("Toronto", "CA", T0, T0 + dt.timedelta(days=200))
    assert sorted(e["id"] for e in got) == sorted(t.isoformat() for t in times)


def event(attractions):
    return {"city_query": "Toronto", "_embedded": {"attractions": attractions}}


def test_performers_dedupes_and_reads_mbid():
    evs = [
        event(
            [
                {"name": "Alvvays", "externalLinks": {"musicbrainz": [{"id": "AB-12"}]}},
                {"name": "Local Opener"},
            ]
        ),
        event([{"name": "Alvvays", "externalLinks": {"musicbrainz": [{"id": "AB-12"}]}}]),
        {"city_query": "Toronto", "_embedded": {}},  # event with no performers listed
    ]
    got = events.performers(evs)
    assert [(p["artist_key"], p["source"]) for p in got] == [
        ("mbid:ab-12", "event:Toronto"),
        ("name:local opener", "event:Toronto"),
    ]


def pool_row(key, name, source="similar:x"):
    return {"artist_key": key, "name": name, "mbid": None, "source": source}


def test_add_event_performers_skips_known_and_respects_cap():
    pool = [pool_row("mbid:1", "Alvvays"), pool_row("name:old", "Old", "event:Toronto")]
    performers = [
        pool_row("name:alvvays", "ALVVAYS", "event:Toronto"),  # same artist by name
        pool_row("name:new1", "New One", "event:Toronto"),
        pool_row("name:new2", "New Two", "event:Montreal"),
    ]
    new_pool, added = add_event_performers(pool, performers, "2026-09-25", cap=2)
    assert added == 1  # cap 2 includes the existing event artist
    assert [r["artist_key"] for r in new_pool] == ["mbid:1", "name:old", "name:new1"]
    assert new_pool[-1]["added_date"] == "2026-09-25"


@pytest.mark.parametrize("cap", [0, -5])
def test_no_budget_adds_nothing(cap):
    pool = [pool_row("a", "A")]
    assert add_event_performers(pool, [pool_row("b", "B", "event:Toronto")], "d", cap)[1] == 0
