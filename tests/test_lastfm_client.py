import pytest
import responses

from anr_radar.clients.lastfm import API_URL, LastFmClient, LastFmError, NotFound


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    return LastFmClient("k", requests_per_sec=1000, max_retries=2)


@responses.activate
def test_single_result_is_wrapped_in_list(client):
    responses.get(API_URL, json={"topartists": {"artist": {"name": "Solo", "mbid": ""}}})
    assert client.geo_top_artists("Canada") == [{"name": "Solo", "mbid": ""}]


@responses.activate
def test_not_found_raises(client):
    responses.get(
        API_URL, json={"error": 6, "message": "The artist you supplied could not be found"}
    )
    with pytest.raises(NotFound):
        client.artist_info("nobody")


@responses.activate
def test_rate_limit_is_retried(client):
    responses.get(API_URL, json={"error": 29, "message": "Rate limit exceeded"})
    responses.get(API_URL, json={"artist": {"name": "X", "stats": {"listeners": "10"}}})
    assert client.artist_info("X")["name"] == "X"
    assert len(responses.calls) == 2


@responses.activate
def test_non_retryable_error_raises(client):
    responses.get(API_URL, json={"error": 10, "message": "Invalid API key"})
    with pytest.raises(LastFmError):
        client.artist_info("X")
    assert len(responses.calls) == 1
