from anr_radar.keys import artist_key, normalize_name


def test_normalize_strips_accents_case_and_whitespace():
    assert normalize_name("  Beyoncé   Knowles ") == "beyonce knowles"
    assert normalize_name("SIGUR RÓS") == normalize_name("Sigur Ros")


def test_artist_key_prefers_mbid():
    assert artist_key("Björk", "87C5DEDD-371D-4A53-9F7F-80522FB7F3CB") == (
        "mbid:87c5dedd-371d-4a53-9f7f-80522fb7f3cb"
    )
    assert artist_key("Björk", None) == "name:bjork"
    assert artist_key("Björk", "") == "name:bjork"
