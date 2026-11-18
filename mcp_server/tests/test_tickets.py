import datetime as dt

import pytest

import tickets

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)


def event(code, start="2026-06-01T14:00:00Z", end="2026-12-01T02:00:00Z", date="2026-12-01"):
    return {
        "dates": {"status": {"code": code}, "start": {"localDate": date}},
        "sales": {"public": {"startDateTime": start, "endDateTime": end}},
    }


@pytest.mark.parametrize(
    "ev, status",
    [
        (event("onsale"), "on sale"),
        (event("canceled"), "canceled"),
        (event("offsale", start="2026-12-01T14:00:00Z"), "not on sale yet"),
        (event("offsale"), "off sale"),  # inside the sale window: maybe sold out
        (event("offsale", end="2026-11-10T02:00:00Z"), "sales closed"),
        (event("rescheduled", date="2027-01-09"), "rescheduled"),
        (None, "unknown"),
    ],
)
def test_classify(ev, status):
    assert tickets.classify(ev, NOW)[0] == status


def test_off_sale_is_never_called_sold_out():
    _, note = tickets.classify(event("offsale"), NOW)
    assert "often sold out" in note and "doesn't say" in note


@pytest.fixture(autouse=True)
def clear_cache():
    tickets._cache.clear()


def shows():
    return {"count": 2, "shows": [{"event_id": "a"}, {"event_id": "b"}], "notes": []}


def test_annotate_batches_once_drops_canceled_and_caches():
    calls = []

    def fetch(key, ids):
        calls.append(ids)
        return {"a": event("onsale"), "b": event("canceled")}

    result = tickets.annotate(shows(), "k", fetch=fetch)
    assert [s["event_id"] for s in result["shows"]] == ["a"] and result["count"] == 1
    assert any("canceled" in n for n in result["notes"])
    tickets.annotate(shows(), "k", fetch=fetch)
    assert calls == [["a", "b"]]  # one batched request; the second answer used the cache


def test_annotate_survives_ticketmaster_being_down():
    def broken(key, ids):
        raise ConnectionError("down")

    result = tickets.annotate(shows(), "k", fetch=broken)
    assert [s["ticket_status"] for s in result["shows"]] == ["unknown", "unknown"]
    no_key = tickets.annotate(shows(), None, fetch=broken)
    assert no_key["shows"][0]["ticket_note"] == "Couldn't check live ticket status."
