import duckdb
import pytest

import export_snapshot as ex
from conftest import fixture_tables


def test_databricks_queries_select_only_needed_columns():
    q = ex.databricks_queries("workspace", "anr_")
    assert set(q) == set(ex.COLUMNS)
    assert q["artist_signals"].startswith("SELECT artist_key, name,")
    assert "ORDER BY d DESC LIMIT 30" in q["artist_growth_history"]  # last 30 snapshot days
    assert q["scout_briefs"].endswith("WHERE error IS NULL")
    assert "FROM workspace.anr_silver.artist_similar" in q["artist_similar"]
    assert "FROM workspace.anr_gold.artist_signals" in q["artist_signals"]


def test_write_snapshot_requires_all_tables(tmp_path):
    tables = fixture_tables()
    del tables["scout_briefs"]
    with pytest.raises(ValueError, match="scout_briefs"):
        ex.write_snapshot(tables, tmp_path / "x.duckdb", "test")


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_from_files_round_trip(tmp_path, fmt):
    """Files exported from the Databricks UI produce the same snapshot, arrays included."""
    folder = tmp_path / "export"
    folder.mkdir()
    for name, df in fixture_tables().items():
        if fmt == "parquet":
            df.to_parquet(folder / f"{name}.parquet")
        else:  # the UI writes arrays as JSON text
            df = df.copy()
            for col in ex.LIST_COLUMNS.get(name, []):
                df[col] = df[col].apply(lambda v: "[" + ",".join(f'"{x}"' for x in v) + "]")
            df.to_csv(folder / f"{name}.csv", index=False)
    out = tmp_path / "snap.duckdb"
    ex.main(["--from-files", str(folder), "--out", str(out)])
    with duckdb.connect(str(out), read_only=True) as con:
        tags = con.execute("SELECT tags FROM artist_signals WHERE name = 'CHXRRY'").fetchall()[0][0]
        source = con.execute("SELECT DISTINCT source FROM snapshot_meta").fetchall()[0][0]
    assert tags == ["indie pop", "bedroom pop"]
    assert source == "files:export"
