"""Personal recommendations, against the fixture snapshot and a fake Last.fm (no network)."""

import logging

import pytest
import responses

import personal
from anr_radar.clients.lastfm import API_URL, LastFmClient, NotFound
from ar_data import QueryError

TOP = {
    "overall": [
        {"name": "Phoebe Bridgers", "mbid": "PB", "playcount": "500"},  # mbid match (any case)
        {"name": "Tinashe", "mbid": "tin", "playcount": "300"},  # not tracked: live similar
        {"name": "Spoons", "mbid": "", "playcount": "200"},  # exact name; must not become Spoon
        {"name": "Zzz Band", "mbid": "", "playcount": "10"},  # not tracked, no similar
    ],
    "3month": [{"name": "Phoebe Bridgers", "mbid": "PB", "playcount": "50"}],
}
SIMILAR = {"Tinashe": [{"name": "CHXRRY", "mbid": "cx", "match": "0.9"}]}


class FakeLastFm:
    def __init__(self, top=TOP, similar=SIMILAR, missing=False):
        self.top, self.similar_map, self.missing, self.calls = top, similar, missing, 0

    def user_info(self, user):
        self.calls += 1
        if self.missing:
            raise NotFound(6, "User not found")
        return {"name": user, "playcount": "12345"}

    def user_top_artists(self, user, period="overall", limit=50):
        self.calls += 1
        return self.top.get(period, [])

    def similar_artists(self, name, limit=15):
        self.calls += 1
        return self.similar_map.get(name, [])


@pytest.fixture(autouse=True)
def clear_cache():
    personal._cache.clear()


def names(rows, field="name"):
    return [r[field] for r in rows]


def test_taste_profile(db):
    p = personal.taste_profile(FakeLastFm(), "testfan", db=db)
    assert names(p["top_artists"]) == ["Phoebe Bridgers", "Tinashe", "Spoons", "Zzz Band"]
    assert [a["tracked"] for a in p["top_artists"]] == [True, False, True, False]
    assert p["coverage"] == 0.5
    assert p["total_scrobbles"] == 12345
    assert "indie pop" in p["top_tags"]
    assert p["your_artists_breaking"] == []  # Phoebe is 1M+ (established), Spoons scores 40
    assert any("1M+" in n for n in p["notes"])
    assert 0 < p["emerging_share"] < 1  # Spoons (<10k) counts, Phoebe (500k-2M) doesn't


def test_mapping_is_exact_never_fuzzy(db):
    with personal.ar_data.connect(db) as con:
        taste = personal.Taste(FakeLastFm(), "testfan", con)
    assert taste.known == {"mbid:pb", "name:spoons"}  # not mbid:sp (Spoon)


def test_discover_excludes_known_aliases_and_explains(db):
    result = personal.discover(FakeLastFm(), "testfan", db=db)
    picks = names(result["artists"])
    assert picks[0] == "CHXRRY"  # two seeds point at it: Phoebe (snapshot) + Tinashe (live)
    assert "Phoebe Bridgers" not in picks  # already in your library
    assert "JAŸ-Z" not in picks and "Big Star Canada" not in picks
    assert "Spoon" not in picks  # related via Spoons, but only scores 60 (< 70)
    assert result["artists"][0]["because"].startswith("similar to Phoebe Bridgers, Tinashe")
    assert "Florence Road" in picks  # reached through a reverse-direction edge


def test_discover_filters(db):
    result = personal.discover(FakeLastFm(), "testfan", genre="indie rock", db=db)
    assert names(result["artists"]) == ["Florence Road"]
    with pytest.raises(QueryError):
        personal.discover(FakeLastFm(), "testfan", size_band="huge", db=db)


def test_shows_for_me_respects_city_price_and_dates(db):
    result = personal.shows_for_me(FakeLastFm(), "testfan", city="Toronto", max_price=30, db=db)
    assert names(result["shows"], "artist_name") == ["CHXRRY", "Maple Kid"]  # Spoon is $60
    assert result["shows"][0]["price_known"] is False
    assert all(s["why"] for s in result["shows"])


def test_cold_start_says_so(db):
    fan = FakeLastFm(top={"overall": [{"name": "Zzz Band", "mbid": "", "playcount": "9"}]})
    result = personal.discover(fan, "newfan", db=db)
    assert result["count"] == 0
    assert any("tags only" in n for n in result["notes"])
    assert any("are tracked" in n for n in result["notes"])


def test_errors_are_actionable(db):
    with pytest.raises(QueryError, match="No Last.fm user"):
        personal.taste_profile(FakeLastFm(missing=True), "ghost", db=db)
    with pytest.raises(QueryError, match="no listening history"):
        personal.taste_profile(FakeLastFm(top={}), "quietfan", db=db)
    fake = FakeLastFm()
    with pytest.raises(QueryError, match="valid Last.fm username"):
        personal.taste_profile(fake, "x'; DROP TABLE artist_signals; --", db=db)
    assert fake.calls == 0  # rejected before any network call


def test_listening_is_cached(db):
    fake = FakeLastFm()
    personal.taste_profile(fake, "testfan", db=db)
    first = fake.calls
    personal.discover(fake, "TestFan", db=db)  # usernames are case-insensitive
    assert fake.calls == first


def test_username_never_logged(db, caplog):
    with caplog.at_level(logging.DEBUG):
        personal.taste_profile(FakeLastFm(), "testfan", db=db)
    assert "testfan" not in caplog.text
    assert "user#" in caplog.text


@responses.activate
def test_real_client_end_to_end(db, monkeypatch):
    """Through the real LastFmClient and HTTP layer (mocked), as the server uses it."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    responses.get(API_URL, json={"user": {"name": "testfan", "playcount": "7"}})
    responses.get(API_URL, json={"topartists": {"artist": TOP["overall"]}})
    responses.get(API_URL, json={"topartists": {"artist": TOP["3month"]}})
    responses.get(API_URL, json={"similarartists": {"artist": SIMILAR["Tinashe"]}})
    responses.get(API_URL, json={"similarartists": {"artist": []}})
    client = LastFmClient("k", requests_per_sec=1000)
    assert names(personal.discover(client, "testfan", db=db)["artists"])[0] == "CHXRRY"


def test_your_artists_breaking_favours_small_and_well_played(db):
    extra = [
        {"name": "Big Star Canada", "mbid": "bs", "playcount": "90"},  # 5M listeners
        {"name": "Florence Road", "mbid": "fr", "playcount": "40"},  # 30k
        {"name": "Maple Kid", "mbid": "", "playcount": "2"},  # 12k, but barely played
    ]
    p = personal.taste_profile(FakeLastFm(top={"overall": [*TOP["overall"], *extra]}), "fan", db=db)
    breaking = p["your_artists_breaking"]
    assert names(breaking) == ["Florence Road", "Maple Kid"]  # no Phoebe (1.5M), no Big Star
    assert breaking[0]["rank_score"] > breaking[1]["rank_score"] > 0
    assert any("2 more" in n and "1M+" in n for n in p["notes"])


def test_smallness_is_a_log_scale():
    assert personal.smallness(5_000) == 1.0
    assert personal.smallness(100_000) == pytest.approx(0.5)
    assert personal.smallness(1_000_000) == personal.smallness(3_000_000) == 0.0
    assert personal.smallness(None) == 0.0


def test_prefer_cheap_lifts_listed_low_prices(db):
    plain = personal.shows_for_me(FakeLastFm(), "testfan", who="new", db=db)["shows"]
    cheap = personal.shows_for_me(FakeLastFm(), "testfan", who="new", prefer_cheap=True, db=db)
    assert plain[0]["artist_name"] == "CHXRRY"  # best taste match, but no listed price
    assert cheap["shows"][0]["artist_name"] == "Florence Road"  # $20
    assert cheap["shows"][0]["score_parts"]["cheap"] == 0.8
    assert cheap["filters"]["prefer_cheap"] is True


def test_cheapness_never_rewards_missing_or_zero_prices():
    assert personal.cheapness(None) == 0 and personal.cheapness(0) == 0
    assert personal.cheapness(25) == 0.75 and personal.cheapness(150) == 0


DEEP = {**TOP, "overall": [*TOP["overall"], {"name": "Maple Kid", "mbid": "", "playcount": "3"}]}


def ranked(result):
    return [s["artist_name"] for s in result["shows"]]


def test_who_shifts_the_ranking_without_excluding_anyone(db):
    """No hard 'yours' vs 'new' groups: who= only moves weight between familiarity and
    novelty, so every linked show stays in the list."""
    fan = FakeLastFm(top=DEEP)
    mine = ranked(personal.shows_for_me(fan, "deepfan", who="mine", days_ahead=90, db=db))
    new = ranked(personal.shows_for_me(fan, "deepfan", who="new", days_ahead=90, db=db))
    assert set(mine) == set(new)  # same shows, different order
    assert mine[0] == "Phoebe Bridgers"  # 500 plays: the most "yours"
    assert new[-1] == "Phoebe Bridgers"  # ...and the least new
    assert mine.index("Maple Kid") < mine.index("CHXRRY")  # 3 plays beats 0 for "mine"
    assert "Big Star Canada" not in mine  # no plays and no taste link: not "for you"
    with pytest.raises(QueryError):
        personal.shows_for_me(fan, "deepfan", who="everyone", db=db)


def test_familiarity_is_a_dial_not_a_category(db):
    with personal.ar_data.connect(db) as con:
        taste = personal.Taste(FakeLastFm(top=DEEP), "deepfan", con)
    assert taste.familiarity("mbid:pb") == 1.0  # most played
    assert 0.2 < taste.familiarity("name:maple kid") < 0.25  # 3 plays: barely
    assert taste.familiarity("mbid:cx") == 0.0  # never played


def test_barely_played_artists_can_still_be_discovered(db):
    picks = personal.discover(FakeLastFm(top=DEEP), "deepfan", db=db)["artists"]
    by_name = {a["name"]: a for a in picks}
    assert "Phoebe Bridgers" not in by_name  # a favourite is never a "discovery"
    assert by_name["Maple Kid"]["your_plays"] == 3  # 3 plays: still suggested, ranked lower
    assert by_name["Maple Kid"]["because"].startswith("you've played them 3 times")
    assert names(picks)[0] == "CHXRRY"


def test_rank_score_is_explained_by_its_parts(db):
    shows = personal.shows_for_me(FakeLastFm(top=DEEP), "deepfan", days_ahead=90, db=db)["shows"]
    phoebe = next(s for s in shows if s["artist_name"] == "Phoebe Bridgers")
    assert phoebe["score_parts"] == {
        "connection": 1.0, "breakout": 0.95, "emerging": 0.0, "billing": 1.0,
    }  # fmt: skip
    assert phoebe["rank_score"] == 0.78  # 0.4 * 1.0 + 0.4 * 0.95 + 0.2 * 0
    assert phoebe["why"].startswith("you've played them 500 times")


def test_opening_slots_say_so_and_rank_lower(db):
    shows = personal.shows_for_me(FakeLastFm(), "testfan", who="new", days_ahead=90, db=db)
    spoon = next(s for s in shows["shows"] if s["artist_name"] == "Spoon")
    assert spoon["why"].startswith("opening for Big Star Canada")
    assert spoon["score_parts"]["billing"] == personal.SUPPORT_SLOT
