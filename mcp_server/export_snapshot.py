"""Export A&R Radar's gold tables (plus the silver similar-artist graph) into a local DuckDB file
for the MCP server.

Two sources, same output file:

    # live: runs SELECTs on a Databricks SQL warehouse, using your `databricks auth login` profile
    python mcp_server/export_snapshot.py --profile pellement

    # offline: Parquet/CSV files downloaded from the Databricks UI, named <table>.parquet|.csv
    python mcp_server/export_snapshot.py --from-files ~/Downloads/ar_radar_export/

Re-run whenever the pipeline refreshes. The file is written to a temp path and swapped in at the
end, so a running server never sees a half-written snapshot.
"""

import argparse
import datetime as dt
import logging
import os
import time
from pathlib import Path

import duckdb

log = logging.getLogger("export_snapshot")

DEFAULT_OUT = Path(__file__).parent / "data" / "ar_radar.duckdb"
HISTORY_DAYS = 30

# Only the columns the server uses. Listing them also documents the snapshot (see SCHEMA.md).
COLUMNS = {
    "artist_signals": [
        "artist_key", "name", "as_of_date", "genres", "tags", "lastfm_listeners",
        "lastfm_playcount", "size_band", "breakout_score", "breakout_rank",
        "listeners_7d_growth_pct", "listeners_14d_growth_pct", "plays_7d_growth_pct",
        "new_listeners_7d", "new_country_charts_14d", "chart_countries", "listener_growth_z",
        "play_growth_z", "is_provisional", "history_days", "growth_window_days",
        "stats_last_changed", "on_tour", "lastfm_url", "possible_alias_of",
    ],
    "artist_growth_history": [
        "artist_key", "name", "snapshot_date", "lastfm_listeners", "lastfm_playcount",
        "listeners_change", "playcount_change",
    ],
    "upcoming_shows": [
        "event_date", "local_time", "city", "venue", "event_name", "event_id", "artist_key",
        "artist_name", "is_headliner", "breakout_score", "breakout_rank", "size_band",
        "lastfm_listeners", "genres", "listeners_7d_growth_pct", "is_provisional", "min_price",
        "max_price", "currency", "ticket_url", "tm_genre", "status", "match_method",
    ],
    "scout_briefs": [
        "artist_key", "name", "as_of_date", "brief_md", "signing_status", "label_mentioned",
        "model", "prompt_version", "generated_at",
    ],
    # Last.fm artist.getSimilar edges from the pool build: the graph for personal recommendations.
    "artist_similar": ["from_key", "to_key", "to_name", "match"],
    # Full bill per show (0 = headliner), so answers can say "opening for X".
    "event_performers": ["event_id", "billing_order", "performer_name"],
}  # fmt: skip
LAYER = {"artist_similar": "silver", "event_performers": "silver"}  # the rest come from gold
# Array columns arrive as JSON text when exported to CSV from the UI.
LIST_COLUMNS = {"artist_signals": ["tags", "chart_countries"]}


def databricks_queries(catalog: str, prefix: str) -> dict[str, str]:
    """One SELECT per table. Schema names come from our own CLI args, never from the model."""

    def table(t: str) -> str:
        return f"{catalog}.{prefix}{LAYER.get(t, 'gold')}.{t}"

    q = {t: f"SELECT {', '.join(cols)} FROM {table(t)}" for t, cols in COLUMNS.items()}
    # Last 30 *snapshot days*, not calendar days: after a gap in daily runs, a calendar window
    # would hold a single day and every trend would read "insufficient history".
    q["artist_growth_history"] += (
        f" WHERE snapshot_date >= (SELECT min(d) FROM (SELECT DISTINCT snapshot_date AS d"
        f" FROM {table('artist_growth_history')} ORDER BY d DESC LIMIT {HISTORY_DAYS}))"
    )
    q["scout_briefs"] += " WHERE error IS NULL"
    return q


def write_snapshot(tables: dict, out: Path, source: str) -> dict[str, int]:
    """tables: name -> pandas DataFrame or pyarrow Table. Returns row counts."""
    missing = set(COLUMNS) - set(tables)
    if missing:
        raise ValueError(f"missing tables: {sorted(missing)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    exported_at = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    counts = {}
    con = duckdb.connect(str(tmp))
    try:
        con.execute("SET TimeZone = 'UTC'")
        for name, data in tables.items():
            con.register("incoming", data)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM incoming")
            con.unregister("incoming")
            # Databricks sends timezone-aware timestamps; store them as plain UTC so readers
            # don't need pytz (DuckDB requires it to return TIMESTAMPTZ values to Python).
            for (col,) in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND data_type = 'TIMESTAMP WITH TIME ZONE'",
                [name],
            ).fetchall():
                con.execute(f'ALTER TABLE {name} ALTER "{col}" TYPE TIMESTAMP')
            counts[name] = con.execute(f"SELECT count(*) FROM {name}").fetchall()[0][0]
        con.execute(
            "CREATE TABLE snapshot_meta (table_name VARCHAR, row_count BIGINT, "
            "exported_at TIMESTAMP, source VARCHAR)"
        )
        con.executemany(
            "INSERT INTO snapshot_meta VALUES (?, ?, ?, ?)",
            [(n, c, exported_at, source) for n, c in counts.items()],
        )
    finally:
        con.close()
    os.replace(tmp, out)  # atomic swap: readers see the old file or the new one, never half
    return counts


def read_files(folder: Path) -> dict:
    """Load <table>.parquet (preferred) or <table>.csv for each table."""
    con = duckdb.connect()
    tables = {}
    for name in COLUMNS:
        parquet, csv = folder / f"{name}.parquet", folder / f"{name}.csv"
        if parquet.exists():
            tables[name] = con.execute("SELECT * FROM read_parquet(?)", [str(parquet)]).df()
        elif csv.exists():
            rel = con.execute("SELECT * FROM read_csv(?)", [str(csv)]).df()
            for col in LIST_COLUMNS.get(name, []):
                # '["indie pop","pop"]' -> list; the UI's CSV download flattens arrays to JSON.
                con.register("rel", rel)
                rel[col] = con.execute(
                    f"SELECT from_json({col}, '[\"VARCHAR\"]') AS v FROM rel"
                ).df()["v"]
                con.unregister("rel")
            tables[name] = rel
        else:
            raise FileNotFoundError(f"expected {parquet.name} or {csv.name} in {folder}")
    return tables


def fetch_databricks(
    profile: str | None, warehouse_id: str | None, catalog: str, prefix: str
) -> dict:
    """Run each query on a SQL warehouse via the Statement Execution API (Arrow results)."""
    from databricks.sdk import WorkspaceClient  # only needed for the live path

    w = WorkspaceClient(profile=profile)
    if not warehouse_id:
        warehouses = list(w.warehouses.list())
        if not warehouses:
            raise SystemExit("No SQL warehouse found; pass --warehouse-id")
        warehouse_id = warehouses[0].id
        log.info("using warehouse %s (%s)", warehouses[0].name, warehouse_id)
    return {
        name: _run_statement(w, warehouse_id, sql)
        for name, sql in databricks_queries(catalog, prefix).items()
    }


def _run_statement(w, warehouse_id: str, sql: str, timeout_s: int = 600):
    import pyarrow as pa
    import requests
    from databricks.sdk.service.sql import Disposition, Format, StatementState

    log.info("running: %s", sql[:120])
    api = w.statement_execution
    resp = api.execute_statement(
        statement=sql,
        warehouse_id=warehouse_id,
        disposition=Disposition.EXTERNAL_LINKS,  # Arrow results come back as download links
        format=Format.ARROW_STREAM,
        wait_timeout="30s",
    )
    deadline = time.monotonic() + timeout_s  # a stopped serverless warehouse takes a while
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        if time.monotonic() > deadline:
            raise TimeoutError(f"statement {resp.statement_id} still running")
        time.sleep(3)
        resp = api.get_statement(resp.statement_id)
    if resp.status.state != StatementState.SUCCEEDED:
        error = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"query failed: {error}")

    parts = []
    for i in range(resp.manifest.total_chunk_count or 0):
        chunk = resp.result if i == 0 else api.get_statement_result_chunk_n(resp.statement_id, i)
        for link in chunk.external_links or []:
            # Pre-signed cloud-storage URL: must NOT carry the Databricks auth header.
            r = requests.get(link.external_link, headers=link.http_headers or {}, timeout=120)
            r.raise_for_status()
            parts.append(pa.ipc.open_stream(r.content).read_all())
    if not parts:  # empty result: build an empty table with the right column names
        cols = [c.name for c in resp.manifest.schema.columns]
        return pa.table({c: pa.array([], pa.string()) for c in cols})
    return pa.concat_tables(parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-files", type=Path, help="folder of <table>.parquet/.csv")
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    parser.add_argument("--catalog", default="workspace")
    parser.add_argument("--prefix", default="anr_", help="schema prefix; dev_anr_ for dev")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.from_files:
        tables, source = read_files(args.from_files), f"files:{args.from_files.name}"
    else:
        tables = fetch_databricks(args.profile, args.warehouse_id, args.catalog, args.prefix)
        source = f"databricks:{args.catalog}.{args.prefix}*"
    counts = write_snapshot(tables, args.out, source)
    for name, n in counts.items():
        log.info("%-22s %7d rows", name, n)
    log.info("wrote %s", args.out)


if __name__ == "__main__":
    main()
