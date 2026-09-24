from anr_radar.transform.bronze import dates_to_load, landed_dates


def test_landed_dates_sorted_and_filtered(tmp_path):
    for d in ["dt=2026-09-24", "dt=2026-09-23", "_tmp", "notes.txt"]:
        (tmp_path / "lastfm" / "artist_info" / d).mkdir(parents=True)
    assert landed_dates(str(tmp_path), "lastfm/artist_info") == ["2026-09-23", "2026-09-24"]
    assert landed_dates(str(tmp_path), "missing") == []


def test_dates_to_load_backfills_gaps_and_reloads_recent():
    landed = ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]
    loaded = {"2026-09-20", "2026-09-22", "2026-09-23", "2026-09-24"}
    assert dates_to_load(landed, loaded, 2) == ["2026-09-21", "2026-09-23", "2026-09-24"]
    assert dates_to_load(landed, set(landed), 0) == []
