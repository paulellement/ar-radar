import pytest

from anr_radar.matching.names import match_performers, normalize

ARTISTS = [
    {"artist_key": "mbid:111", "name": "The National", "mbid": "111"},
    {"artist_key": "name:haim", "name": "HAIM", "mbid": None},
    {"artist_key": "name:ajr", "name": "AJR", "mbid": None},
    {"artist_key": "mbid:222", "name": "Simon & Garfunkel", "mbid": "222"},
    {"artist_key": "name:sigur ros", "name": "Sigur Rós", "mbid": None},
    {"artist_key": "name:black country, new road", "name": "Black Country, New Road", "mbid": None},
    {
        "artist_key": "name:the jimi hendrix experience",
        "name": "The Jimi Hendrix Experience",
        "mbid": None,
    },
    {"artist_key": "mbid:333", "name": "Alvvays", "mbid": "333"},
]


def match(name, mbid=None):
    [m] = match_performers(
        [{"attraction_id": "a1", "performer_name": name, "performer_mbid": mbid}], ARTISTS
    )
    return m


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The National", "national"),
        ("Simon & Garfunkel", "simon and garfunkel"),
        ("SIGUR RÓS", "sigur ros"),
        ("Black Country, New Road", "black country new road"),
        ("The The", "the"),  # a band literally called "The The" keeps a name
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_mbid_beats_a_different_name():
    m = match("Alvvays (Live)", mbid="333")
    assert (m["artist_key"], m["match_method"]) == ("mbid:333", "mbid")


def test_exact_after_normalizing():
    assert match("National")["match_method"] == "exact"
    assert match("Haim")["artist_key"] == "name:haim"
    assert match("Sigur Ros")["artist_key"] == "name:sigur ros"
    assert match("Black Country New Road")["match_method"] == "exact"


def test_close_long_name_is_fuzzy_auto():
    m = match("Black Country, New Roads")
    assert (m["artist_key"], m["match_method"]) == ("name:black country, new road", "fuzzy")


def test_borderline_goes_to_review():
    m = match("Simon and Garfunkle")  # typo: similar enough to flag, not to trust
    assert m["match_method"] == "review" and m["artist_key"] == "mbid:222"


def test_short_names_never_fuzzy_match():
    assert match("AJJ")["match_method"] == "none"


def test_tribute_acts_are_not_the_artist():
    m = match("Old Friends: A Simon & Garfunkel Tribute")
    assert m["match_method"] == "tribute" and m["artist_key"] is None


def test_experience_in_a_real_band_name_is_not_a_tribute():
    assert match("The Jimi Hendrix Experience")["match_method"] == "exact"


def test_unknown_artist():
    m = match("Completely Unknown Local Band")
    assert m["match_method"] == "none" and m["artist_key"] is None
