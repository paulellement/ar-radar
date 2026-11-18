"""Run the whole Streamlit app headless on the fixture snapshot, with fake Last.fm and
Ticketmaster (no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ar_data
import personal
import tickets
from conftest import AS_OF, fixture_tables
from export_snapshot import write_snapshot
from test_personal import FakeLastFm

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = tmp_path / "snap.duckdb"
    write_snapshot(fixture_tables(), db, "test")
    monkeypatch.setenv("AR_RADAR_DB", str(db))
    monkeypatch.setenv("LASTFM_USERNAME", "")
    monkeypatch.setattr(ar_data, "_today", lambda: AS_OF)
    monkeypatch.setattr(personal, "client", lambda: FakeLastFm())
    monkeypatch.setattr(tickets, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        tickets,
        "fetch_events",
        lambda key, ids: {i: {"id": i, "dates": {"status": {"code": "onsale"}}} for i in ids},
    )
    personal._cache.clear()
    tickets._cache.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return " ".join(parts + [b.label for b in at.button])  # artist names are buttons


def test_loads_without_a_username(app):
    assert not app.exception
    assert "Enter your Last.fm username" in " ".join(i.value for i in app.info)
    assert "Rankings as of" in text(app)


def test_username_personalizes_shows_and_discovery(app):
    app.text_input(key="username").set_value("testfan").run()
    assert not app.exception
    page = text(app)
    assert "CHXRRY" in page  # top discovery and a Toronto show
    assert "similar to Phoebe Bridgers" in page


def test_rising_tab_needs_no_username(app):
    assert "Maple Kid" in text(app)  # the ranking table renders without a username


def test_any_city_searches_live(app, monkeypatch):
    import live_events
    from test_live_events import EVENTS

    live_events._cache.clear()
    monkeypatch.setattr(live_events, "search_events", lambda city, country, days: EVENTS)
    app.text_input(key="city").set_value("Chicago").run()
    assert not app.exception
    assert "Rising artists playing Chicago" in " ".join(h.value for h in app.subheader)
    assert "Opening for Phoebe Bridgers" in text(app)  # CHXRRY's support slot


def test_no_emoji_in_the_interface(app):
    import re

    app.text_input(key="username").set_value("testfan").run()
    emoji = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2139\u24c2]")
    labels = [t.label for t in app.tabs] + [m.value for m in app.markdown]
    assert not [x for x in labels if emoji.search(x)]


def test_clicking_an_artist_opens_their_profile(app):
    app.button(key="open-rising-0").click().run()  # top of the Rising table
    assert not app.exception
    page = text(app)
    assert "Last.fm listeners" in " ".join(h.value for h in app.subheader)
    assert "Shows in Toronto" in " ".join(h.value for h in app.subheader)
    assert "on Ticketmaster in the next 90 days" in page or "Price not listed" in page


def test_missing_price_is_flagged(app):
    app.text_input(key="username").set_value("testfan").run()
    page = text(app)
    assert '<span class="warn">Price not listed</span>' in page  # red, even when ranked first
    assert "public API, which often leaves them out" in page


def test_untracked_artists_say_they_have_no_profile(app):
    app.text_input(key="username").set_value("testfan").run()
    assert 'Tinashe<span class="noprofile">no profile</span>' in text(app)  # not in our pool
    assert not any(b.label == "Tinashe" for b in app.button)
