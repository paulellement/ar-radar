"""Live city search: any city outside the snapshot, with a fake Ticketmaster (no network)."""

import datetime as dt

import pytest

import ar_data
import live_events
import personal
import tickets
from ar_data import QueryError
from conftest import AS_OF
from test_personal import FakeLastFm


def event(eid, days, bill, status="onsale", price=None, venue={"name": "Metro"}):  # noqa: B006
    """A Discovery API event in Chicago. bill: [(name, musicbrainz id or None)], headliner first."""
    return {
        "id": eid,
        "name": f"{bill[0][0]} live",
        "url": f"https://tm.example/{eid}",
        "dates": {
            "start": {"localDate": (AS_OF + dt.timedelta(days=days)).isoformat(),
                      "localTime": "19:30:00"},
            "status": {"code": status},
        },
        "priceRanges": [{"min": price, "max": price, "currency": "USD"}] if price else [],
        "_embedded": {
            "venues": [venue | {"city": {"name": "Chicago"}}],
            "attractions": [
                {"id": f"att-{name}", "name": name,
                 "externalLinks": {"musicbrainz": [{"id": mbid}]} if mbid else {}}
                for name, mbid in bill
            ],
        },
    }  # fmt: skip


EVENTS = [
    event("e1", 3, [("Phoebe Bridgers", "pb"), ("CHXRRY", "cx")], price=45),  # mbid, support
    event("e2", 6, [("Maple Kid", None)], venue={"address": {"line1": "1 Main St"}}),  # name
    event("e3", 8, [("CHXRRY", "cx")], status="canceled"),  # dropped
    event("e4", 9, [("Some Local Band", None)]),  # not tracked: can't be ranked
    event("e5", 60, [("Florence Road", "fr")]),  # outside a 30-day window
]


@pytest.fixture(autouse=True)
def clear_caches():
    live_events._cache.clear()
    tickets._cache.clear()
    personal._cache.clear()


class FakeSearch:
    def __init__(self, events=EVENTS):
        self.events, self.calls = events, []

    def __call__(self, city, country, days):
        self.calls.append((city, country, days))
        return self.events


def test_snapshot_cities_are_not_searched_live(db):
    search = FakeSearch()
    for city in ("Toronto", "montréal", "MONTREAL", None, " "):
        assert live_events.for_city(city, db=db, search=search) is None
    assert search.calls == []


def test_other_cities_are_searched_once_and_cached(db):
    search = FakeSearch()
    live = live_events.for_city("Chicago", "us", 30, db=db, search=search)
    assert live_events.for_city("chicago", "US", 14, db=db, search=search) is live
    assert search.calls == [("Chicago", "US", 30)]  # fetched as one 30-day block, code upper
    assert live["events"].num_rows == 4  # the canceled show is dropped
    assert "e1" in tickets._cache  # ticket status needs no second request


def test_performers_match_the_pool_like_the_pipeline(db):
    live = live_events.for_city("Chicago", db=db, search=FakeSearch())
    matched = {(m["event_id"], m["artist_name"]) for m in live["matches"].to_pylist()}
    assert matched == {("e1", "Phoebe Bridgers"), ("e1", "CHXRRY"), ("e2", "Maple Kid"),
                       ("e5", "Florence Road")}  # fmt: skip


def test_live_shows_answer_like_the_snapshot(db):
    live = live_events.for_city("Chicago", db=db, search=FakeSearch())
    r = ar_data.upcoming_shows(city="Chicago", days_ahead=30, live=live, db=db)
    assert r["filters"]["city"] == "Chicago"
    shows = {s["artist_name"]: s for s in r["shows"]}
    assert list(shows) == ["CHXRRY", "Phoebe Bridgers", "Maple Kid"]  # by breakout score
    assert shows["CHXRRY"]["role"] == "support"
    assert shows["CHXRRY"]["headliner"] == "Phoebe Bridgers"
    assert shows["Phoebe Bridgers"]["currency"] == "USD"
    assert shows["Maple Kid"]["venue"] == "1 Main St"  # no venue name: street address
    assert any("Searched live" in n for n in r["notes"])
    rising = ar_data.upcoming_shows(min_breakout_score=96, live=live, db=db)
    assert [s["artist_name"] for s in rising["shows"]] == ["CHXRRY"]


def test_shows_for_me_in_a_live_city(db):
    live = live_events.for_city("Chicago", db=db, search=FakeSearch())
    r = personal.shows_for_me(FakeLastFm(), "testfan", city="Chicago", live=live, db=db)
    assert {s["artist_name"] for s in r["shows"]} >= {"Phoebe Bridgers", "CHXRRY"}


def test_unknown_city_and_bad_country_are_errors(db):
    with pytest.raises(QueryError, match="no music events"):
        live_events.for_city("Atlantis", db=db, search=FakeSearch(events=[]))
    with pytest.raises(QueryError, match="country code"):
        live_events.for_city("London", "England", db=db, search=FakeSearch())
