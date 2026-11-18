"""Smoke tests through the MCP layer: what a client would see."""

import asyncio

import pytest

import personal
import server
import tickets

SNAPSHOT_TOOLS = {"top_breakout_artists", "artist_profile", "upcoming_shows", "data_status"}
PERSONAL_TOOLS = {"my_taste_profile", "discover_for_me", "shows_for_me"}
LIVE_TOOLS = PERSONAL_TOOLS | {"artist_profile", "upcoming_shows"}  # call Last.fm/Ticketmaster


@pytest.fixture(autouse=True)
def fake_ticketmaster(monkeypatch):
    """No network in tests: CHXRRY's Toronto date is canceled, everything else on sale."""
    tickets._cache.clear()
    monkeypatch.setenv("TICKETMASTER_API_KEY", "test-key")

    def fetch(api_key, ids):
        code = lambda i: "canceled" if i == "ev-mbid:cx-10" else "onsale"  # noqa: E731
        return {i: {"id": i, "dates": {"status": {"code": code(i)}}} for i in ids}

    monkeypatch.setattr(server, "_ticket_fetch", fetch)


def run(coro):
    return asyncio.run(coro)


def test_lists_tools_resources_and_prompt():
    tools = run(server.mcp.list_tools())
    assert {t.name for t in tools} == SNAPSHOT_TOOLS | PERSONAL_TOOLS
    assert all(t.annotations.read_only_hint for t in tools)
    # Only tools that call Last.fm or Ticketmaster live are open-world.
    assert {t.name for t in tools if t.annotations.open_world_hint} == LIVE_TOOLS
    assert all(t.description for t in tools)
    resources = {str(r.uri) for r in run(server.mcp.list_resources())}
    assert resources == {"ar-radar://definitions", "ar-radar://limitations"}
    assert {p.name for p in run(server.mcp.list_prompts())} == {"scout_report", "my_report"}


@pytest.fixture
def snapshot_env(db, monkeypatch):
    monkeypatch.setenv("AR_RADAR_DB", str(db))


def test_tool_call_returns_structured_result(snapshot_env):
    result = run(server.mcp.call_tool("top_breakout_artists", {"genre": "indie pop"}))
    assert not result.is_error
    assert result.structured_content["count"] == 3


def test_bad_input_is_an_error_payload_not_an_exception(snapshot_env):
    result = run(server.mcp.call_tool("upcoming_shows", {"max_price": -1}))
    assert result.structured_content["error"] == "max_price can't be negative"
    assert result.structured_content["hint"]


def test_missing_snapshot_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("AR_RADAR_DB", str(tmp_path / "missing.duckdb"))
    result = run(server.mcp.call_tool("data_status", {}))
    assert "No snapshot" in result.structured_content["error"]


def test_resource_reads():
    contents = list(run(server.mcp.read_resource("ar-radar://limitations")))
    assert "does not predict" in contents[0].content


def test_personal_tool_uses_configured_username(snapshot_env, monkeypatch):
    from test_personal import FakeLastFm

    personal._cache.clear()
    monkeypatch.setattr(server, "_client", lambda: FakeLastFm())
    monkeypatch.setenv("LASTFM_USERNAME", "testfan")
    result = run(server.mcp.call_tool("discover_for_me", {}))
    assert result.structured_content["artists"][0]["name"] == "CHXRRY"
    monkeypatch.delenv("LASTFM_USERNAME")
    result = run(server.mcp.call_tool("my_taste_profile", {}))
    assert "Ask the user" in result.structured_content["hint"]


def test_shows_get_live_ticket_status_and_canceled_ones_drop(snapshot_env):
    result = run(server.mcp.call_tool("upcoming_shows", {"city": "Toronto"})).structured_content
    names = [s["artist_name"] for s in result["shows"]]
    assert "CHXRRY" not in names  # Ticketmaster now says canceled
    assert all(s["ticket_status"] == "on sale" for s in result["shows"])
    assert any("canceled" in n for n in result["notes"])


def test_any_city_is_searched_live(snapshot_env, monkeypatch):
    import live_events
    from test_live_events import EVENTS

    live_events._cache.clear()
    monkeypatch.setattr(server, "_city_search", lambda city, country, days: EVENTS)
    args = {"city": "Chicago", "country_code": "US"}
    result = run(server.mcp.call_tool("upcoming_shows", args)).structured_content
    assert [s["artist_name"] for s in result["shows"]] == ["CHXRRY", "Phoebe Bridgers", "Maple Kid"]
    assert all(s["ticket_status"] == "on sale" for s in result["shows"])
