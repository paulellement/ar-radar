import datetime as dt

from anr_radar.quality import evaluate

TODAY = dt.date(2026, 9, 24)


def healthy(**overrides):
    m = {
        "latest_date": TODAY,
        "pool_size": 5000,
        "latest_rows": 4968,
        "duplicate_rows": 0,
        "bronze_latest_rows": 4997,
        "bronze_latest_distinct_names": 4968,
        "gold_rows": 4968,
        "gold_bad_scores": 0,
    }
    return {**m, **overrides}


def test_healthy_passes():
    assert evaluate(healthy(), TODAY) == []
    assert evaluate(healthy(latest_date=TODAY - dt.timedelta(days=1)), TODAY) == []


def test_each_rule_fires():
    cases = {
        "stale": healthy(latest_date=TODAY - dt.timedelta(days=2)),
        "low coverage": healthy(
            latest_rows=4000, bronze_latest_distinct_names=4000, gold_rows=4000
        ),
        "duplicate": healthy(duplicate_rows=3),
        "silver lost": healthy(latest_rows=4800, gold_rows=4800),
        "gold has": healthy(gold_rows=10),
        "outside 0-100": healthy(gold_bad_scores=1),
    }
    for expected, metrics in cases.items():
        failures = evaluate(metrics, TODAY)
        assert len(failures) == 1 and expected in failures[0], (expected, failures)


def test_empty_tables_fail_rather_than_crash():
    assert evaluate(healthy(latest_date=None), TODAY)
