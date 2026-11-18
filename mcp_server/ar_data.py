"""Read-only queries over the A&R Radar DuckDB snapshot. No MCP code here, so it tests on its own.

Rules every function follows:
- opens its own read-only connection (cheap, and a re-export is picked up without a restart);
- user input reaches SQL only as bound `?` parameters, never via f-strings;
- results are capped at MAX_LIMIT rows and returned as JSON-safe dicts;
- bad input raises QueryError(message, hint), which the server turns into an error payload.

Rankings are as of the snapshot's as_of_date. Show windows start at the later of that date and
the real date: a stale snapshot must never list gigs that have already happened.
"""

import datetime as dt
import math
import os
import unicodedata
from pathlib import Path

import duckdb
from rapidfuzz import fuzz, process

from anr_radar import config
from anr_radar.matching.names import AUTO_THRESHOLD, MIN_FUZZY_LEN, normalize

DEFAULT_DB = Path(__file__).parent / "data" / "ar_radar.duckdb"
MAX_LIMIT = 50
SIZE_BANDS = [label for _, label in config.SIZE_BANDS]
SUPERSTAR_BAND = "2M+"
STALE_AFTER_DAYS = 2  # the pipeline runs daily; older than this is worth telling the user

# Last.fm chart country names (config.SEED_COUNTRIES) -> (ISO code, nationality tag word).
COUNTRIES = {
    "Canada": ("CA", "canadian"), "United States": ("US", "american"),
    "United Kingdom": ("GB", "british"), "Ireland": ("IE", "irish"),
    "Australia": ("AU", "australian"), "Brazil": ("BR", "brazilian"),
    "Mexico": ("MX", "mexican"), "Argentina": ("AR", "argentine"),
    "Colombia": ("CO", "colombian"), "Chile": ("CL", "chilean"), "Spain": ("ES", "spanish"),
    "France": ("FR", "french"), "Germany": ("DE", "german"), "Italy": ("IT", "italian"),
    "Netherlands": ("NL", "dutch"), "Sweden": ("SE", "swedish"), "Poland": ("PL", "polish"),
    "Nigeria": ("NG", "nigerian"), "South Africa": ("ZA", "south african"),
    "Japan": ("JP", "japanese"), "Korea, Republic of": ("KR", "korean"),
    "Philippines": ("PH", "filipino"),
}  # fmt: skip
COUNTRY_ALIASES = {
    "usa": "United States",
    "uk": "United Kingdom",
    "south korea": "Korea, Republic of",
    "korea": "Korea, Republic of",
}

ARTIST_COLUMNS = """name, artist_key, breakout_score, breakout_rank, size_band, lastfm_listeners,
    listeners_7d_growth_pct, listeners_14d_growth_pct, plays_7d_growth_pct, new_listeners_7d,
    chart_countries, genres, is_provisional, lastfm_url"""


def _tag_match(col: str = "tags") -> str:
    """SQL: any tag contains the bound value, case-insensitively ("indie pop" ~ "Indie Pop")."""
    return f"len(list_filter({col}, lambda t: contains(lower(t), lower(?)))) > 0"


# One row per show: the bill in order, so a support slot can name its headliner.
LINEUP_JOIN = """LEFT JOIN (
    SELECT event_id, list(performer_name ORDER BY billing_order) AS bill
    FROM {performers} GROUP BY event_id) lu USING (event_id)"""
LINEUP_COLUMNS = """CASE WHEN {t}is_headliner THEN 'headliner' ELSE 'support' END AS role,
    lu.bill[1] AS headliner, lu.bill[1:6] AS lineup, len(lu.bill) AS lineup_size"""


# A live city search (live_events) shaped like the snapshot's upcoming_shows table.
LIVE_SHOWS = """(SELECT e.*, m.artist_key, coalesce(a.name, m.artist_name) AS artist_name,
        m.billing_order = 0 AS is_headliner, a.breakout_score, a.size_band,
        a.lastfm_listeners, a.genres, a.listeners_7d_growth_pct
    FROM live_events e JOIN live_matches m USING (event_id)
    LEFT JOIN artist_signals a USING (artist_key))"""


class QueryError(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


# --- helpers -------------------------------------------------------------------------------


def db_path(db: str | Path | None = None) -> Path:
    return Path(db or os.environ.get("AR_RADAR_DB") or DEFAULT_DB)


def connect(db: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    path = db_path(db)
    if not path.exists():
        raise QueryError(
            f"No snapshot found at {path}",
            "Run `python mcp_server/export_snapshot.py --profile <profile>` first.",
        )
    return duckdb.connect(str(path), read_only=True)


def _jsonable(v):
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 3)
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [{c: _jsonable(v) for c, v in zip(cols, row, strict=True)} for row in cur.fetchall()]


def _cap(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _as_of(con) -> dt.date:
    return con.execute("SELECT max(as_of_date) FROM artist_signals").fetchall()[0][0]


def _today() -> dt.date:
    return dt.date.today()  # a function so tests can pin it


def data_notes(con) -> list[str]:
    """Caveats about the snapshot itself, attached to every answer that cites growth."""
    as_of = _as_of(con)
    notes = []
    age = (_today() - as_of).days
    if age > STALE_AFTER_DAYS:
        notes.append(
            f"Data is {age} days old (as of {as_of}); growth describes that date, not now."
        )
    # After a gap in daily snapshots the "7-day" growth is measured over the whole gap.
    window = con.execute(
        "SELECT median(growth_window_days) FROM artist_signals WHERE breakout_score IS NOT NULL"
    ).fetchall()[0][0]
    if window and window > config.SHORT_WINDOW_DAYS:
        since = as_of - dt.timedelta(days=int(window))
        notes.append(
            f"'7-day' growth is currently measured over {int(window)} days (snapshots missing "
            f"before {as_of}): read it as growth since {since}, not this week."
        )
    return notes


def _fold(text: str) -> str:
    """Lowercase and strip accents, so 'Montréal' == 'montreal'."""
    s = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def resolve_country(text: str) -> str:
    """'CA', 'canada', 'Canadian' -> 'Canada' (the Last.fm chart name)."""
    key = _fold(text)
    for name, (code, demonym) in COUNTRIES.items():
        if key in (_fold(name), code.lower(), demonym):
            return name
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    raise QueryError(
        f"Unknown or untracked country: {text!r}",
        "Tracked countries: " + ", ".join(f"{n} ({c})" for n, (c, _) in COUNTRIES.items()),
    )


def resolve_size_band(text: str) -> str:
    for band in SIZE_BANDS:
        if text.strip().lower() == band.lower():
            return band
    raise QueryError(f"Unknown size band: {text!r}", f"Use one of: {', '.join(SIZE_BANDS)}")


# --- 1. ranking ---------------------------------------------------------------------------


def top_breakout_artists(
    country: str | None = None,
    genre: str | None = None,
    size_band: str | None = None,
    include_superstars: bool = False,
    limit: int = 10,
    db: str | Path | None = None,
) -> dict:
    # Same base rules as the Genie space: scored artists only, never restyled-alias pages.
    where = ["breakout_score IS NOT NULL", "possible_alias_of IS NULL"]
    params: list = []
    select_extra = ""
    filters: dict = {}
    if size_band:
        filters["size_band"] = resolve_size_band(size_band)
        where.append("size_band = ?")
        params.append(filters["size_band"])
    elif not include_superstars:
        where.append("size_band <> ?")
        params.append(SUPERSTAR_BAND)
    if genre and genre.strip():
        filters["genre"] = genre.strip()
        where.append(_tag_match())
        params.append(filters["genre"])
    if country and country.strip():
        name = filters["country"] = resolve_country(country)
        demonym = COUNTRIES[name][1]
        # Chart presence is the stronger evidence; nationality tags are the fallback.
        in_chart = "list_contains(chart_countries, ?)"
        by_tag = (
            "len(list_filter(tags, lambda t: lower(t) IN (?, ?) OR starts_with(lower(t), ?))) > 0"
        )
        where.append(f"({in_chart} OR {by_tag})")
        params += [name, demonym, name.lower(), demonym + " "]
        select_extra = f", CASE WHEN {in_chart} THEN 'chart' ELSE 'tag' END AS country_evidence"
        params = [name, *params]  # the SELECT's placeholder comes first

    sql = f"""SELECT {ARTIST_COLUMNS}{select_extra} FROM artist_signals
              WHERE {" AND ".join(where)}
              ORDER BY breakout_score DESC, lastfm_listeners LIMIT ?"""
    with connect(db) as con:
        artists = _rows(con, sql, [*params, _cap(limit)])
        as_of = _as_of(con)
        notes = data_notes(con)
    if not include_superstars and not size_band:
        notes.append("Superstars (2M+ listeners) excluded; set include_superstars to include.")
    if any(a["is_provisional"] for a in artists):
        notes.append("is_provisional rows have < 7 days of history; treat growth as early.")
    return {
        "as_of_date": _jsonable(as_of),
        "filters": filters,
        "count": len(artists),
        "artists": artists,
        "notes": notes,
    }


# --- 2. lookup ----------------------------------------------------------------------------


def find_artist(name: str, db: str | Path | None = None) -> dict:
    """Exact (normalized) name first; then fuzzy, with the pipeline's own thresholds.

    Same rules as matching/names.py: auto-accept only at >= 95 similarity and only for names of
    5+ characters, so "Spoon" never silently becomes "Spoons". Otherwise return candidates and
    matched=False so the model asks the user instead of guessing.
    """
    query = normalize(name or "")
    if not query:
        raise QueryError("Empty artist name", "Pass the artist's name, e.g. 'Phoebe Bridgers'.")
    with connect(db) as con:
        rows = _rows(
            con,
            "SELECT artist_key, name, size_band, lastfm_listeners, possible_alias_of "
            "FROM artist_signals",
        )
    by_norm: dict[str, list[dict]] = {}
    for r in rows:
        by_norm.setdefault(normalize(r["name"]), []).append(r)

    def best_of(same_name: list[dict]) -> dict:
        # Prefer the real page over an alias, then the bigger page.
        return min(same_name, key=lambda r: (r["possible_alias_of"] is not None,
                                             -(r["lastfm_listeners"] or 0)))  # fmt: skip

    candidates = process.extract(query, list(by_norm), scorer=fuzz.token_sort_ratio, limit=4)
    alternatives = [
        {"name": best_of(by_norm[c])["name"], "score": round(s, 1),
         "size_band": best_of(by_norm[c])["size_band"]}
        for c, s, _ in candidates if c != query
    ][:3]  # fmt: skip

    if query in by_norm:
        return {"matched": True, "method": "exact", "artist": best_of(by_norm[query]),
                "alternatives": alternatives}  # fmt: skip
    top = candidates[0] if candidates else None
    runner_up = candidates[1][1] if len(candidates) > 1 else 0
    if (
        top
        and top[1] >= AUTO_THRESHOLD
        and len(query) >= MIN_FUZZY_LEN
        and runner_up < AUTO_THRESHOLD  # two near-identical candidates = ambiguous: ask
    ):
        return {"matched": True, "method": "fuzzy", "score": round(top[1], 1),
                "artist": best_of(by_norm[top[0]]), "alternatives": alternatives[1:]}  # fmt: skip
    return {"matched": False, "query": name, "alternatives": alternatives}


# --- 3. profile ---------------------------------------------------------------------------


def trend_summary(points: list[dict]) -> dict:
    """Is the growth spread over many days or one jump? points: snapshot_date, lastfm_listeners,
    listeners_change (ascending dates)."""
    if len(points) < 3:
        return {"pattern": "insufficient_history", "days": len(points)}
    first, last = points[0], points[-1]
    total = (last["lastfm_listeners"] or 0) - (first["lastfm_listeners"] or 0)
    # A change across a gap in snapshots (e.g. a month the pipeline didn't run) is spread over
    # the days it covers; otherwise one catch-up row would look like a one-day spike.
    steps, gaps = [], []
    for prev, p in zip(points, points[1:], strict=False):
        days = (
            dt.date.fromisoformat(p["snapshot_date"]) - dt.date.fromisoformat(prev["snapshot_date"])
        ).days  # noqa: E501
        days = max(days, 1)
        steps.append(((p["listeners_change"] or 0) / days, p["snapshot_date"], days))
        if days > 1:
            gaps.append({"from": prev["snapshot_date"], "to": p["snapshot_date"], "days": days})
    biggest, biggest_day, _ = max(steps)
    biggest = round(biggest)
    up_days = sum(days for change, _, days in steps if change > 0)
    share = biggest / total if total > 0 else None
    if total <= 0:
        pattern = "flat"
    elif share > 0.5 or up_days < 3:
        pattern = "concentrated"
    else:
        pattern = "steady"
    return {
        "pattern": pattern,
        "days": len(points),
        "from": first["snapshot_date"],
        "to": last["snapshot_date"],
        "listeners_start": first["lastfm_listeners"],
        "listeners_end": last["lastfm_listeners"],
        "total_change": total,
        "days_with_increase": up_days,
        "largest_one_day_change": biggest,
        "largest_one_day_date": biggest_day,
        "largest_one_day_share": _jsonable(share),
        "gaps": gaps,  # spans with no snapshots; changes there are averaged per day
    }


def artist_profile(
    name: str | None = None,
    db: str | Path | None = None,
    artist_key: str | None = None,  # internal (web app): an exact key, no name matching
) -> dict:
    if artist_key:
        match, key = {"method": "key", "alternatives": []}, artist_key
    else:
        match = find_artist(name or "", db)
        if not match["matched"]:
            return match
        key = match["artist"]["artist_key"]
    with connect(db) as con:
        found = _rows(
            con,
            f"""SELECT {ARTIST_COLUMNS}, as_of_date, tags[1:10] AS tags, lastfm_playcount,
                new_country_charts_14d, listener_growth_z, play_growth_z, history_days,
                growth_window_days, stats_last_changed, on_tour, possible_alias_of
                FROM artist_signals WHERE artist_key = ?""",
            [key],
        )
        if not found:
            raise QueryError(f"No tracked artist with key {key!r}", "Search by name instead.")
        signals = found[0]
        history = _rows(
            con,
            """SELECT snapshot_date, lastfm_listeners, listeners_change
               FROM artist_growth_history WHERE artist_key = ? ORDER BY snapshot_date""",
            [key],
        )
        briefs = _rows(
            con,
            """SELECT brief_md, signing_status, label_mentioned, as_of_date, generated_at
               FROM scout_briefs WHERE artist_key = ? AND brief_md IS NOT NULL
               ORDER BY generated_at DESC LIMIT 1""",
            [key],
        )
        shows = _rows(
            con,
            f"""SELECT event_date, local_time, city, venue, event_name, min_price, max_price,
                      currency, ticket_url, event_id, status,
                      min_price IS NOT NULL AS price_known, {LINEUP_COLUMNS.format(t="")}
               FROM upcoming_shows {LINEUP_JOIN.format(performers="event_performers")}
               WHERE artist_key = ? AND event_date >= ?
               ORDER BY event_date LIMIT ?""",
            [key, max(_as_of(con), _today()), MAX_LIMIT],
        )
    return {
        "matched": True,
        "match_method": match["method"],
        "signals": signals,
        "trend": trend_summary(history),
        "daily_listeners": [
            {"date": h["snapshot_date"], "listeners": h["lastfm_listeners"]} for h in history
        ],
        "scout_brief": briefs[0] if briefs else None,
        "upcoming_shows": shows,
        "other_possible_matches": match["alternatives"],
    }


# --- 4. shows -----------------------------------------------------------------------------


def upcoming_shows(
    city: str | None = None,
    max_price: float | None = None,
    days_ahead: int = 30,
    min_breakout_score: float | None = None,
    genre: str | None = None,
    require_price: bool = False,
    limit: int = 20,
    db: str | Path | None = None,
    artist_keys: list[str] | None = None,  # internal (personal.shows_for_me), not a tool arg
    max_rows: int | None = None,  # internal: personal ranking re-sorts every match itself
    live: dict | None = None,  # internal: a live city search (live_events.for_city)
) -> dict:
    if not 1 <= days_ahead <= config.EVENT_HORIZON_DAYS:
        horizon = config.EVENT_HORIZON_DAYS
        raise QueryError(
            f"days_ahead must be 1-{horizon}", f"Shows are collected {horizon} days out."
        )
    if max_price is not None and max_price < 0:
        raise QueryError("max_price can't be negative", "Prices are in CAD, e.g. 30.")
    if min_breakout_score is not None and not 0 <= min_breakout_score <= 100:
        raise QueryError("min_breakout_score must be 0-100", "70+ is a common 'rising' cut-off.")

    with connect(db) as con:
        as_of = _as_of(con)
        start = max(as_of, _today())
        until = start + dt.timedelta(days=days_ahead)
        where = ["u.event_date BETWEEN ? AND ?"]
        params: list = [start, until]
        filters: dict = {"days_ahead": days_ahead}
        shows_table, performers = "upcoming_shows", "event_performers"
        if live is not None:
            # Same query over the live search's tables (already one city), shaped like the
            # snapshot's upcoming_shows by joining the tracked artists' signals.
            for name in ("events", "performers", "matches"):
                con.register(f"live_{name}", live[name])
            shows_table, performers = LIVE_SHOWS, "live_performers"
            filters["city"] = live["city"]
        elif city and city.strip():
            hits = tracked_city_names(con, city)
            if not hits:
                cities = ", ".join(tracked_city_names(con))
                raise QueryError(f"No shows tracked in {city!r}", f"Cities: {cities}")
            # Ticketmaster spells one city several ways ("Montréal" and "Montreal"): keep all.
            filters["city"] = hits[0] if len(hits) == 1 else hits
            where.append("list_contains(?, u.city)")
            params.append(hits)
        if max_price is not None:
            # Unknown prices stay in (flagged): missing is not $0, and dropping them would hide
            # ~88% of shows.
            filters["max_price"] = max_price
            where.append("(u.min_price <= ? OR u.min_price IS NULL)")
            params.append(max_price)
        if artist_keys is not None:
            where.append("list_contains(?, u.artist_key)")
            params.append(artist_keys)
        if require_price:
            filters["require_price"] = True
            where.append("u.min_price IS NOT NULL")
        if min_breakout_score is not None:
            filters["min_breakout_score"] = min_breakout_score
            where.append("u.breakout_score >= ?")
            params.append(min_breakout_score)
        if genre and genre.strip():
            filters["genre"] = genre.strip()
            where.append(_tag_match("s.tags"))
            params.append(filters["genre"])
        tables = f"{shows_table} u LEFT JOIN artist_signals s USING (artist_key)"
        condition = " AND ".join(where)
        # Totals beyond the row limit: most shows have no price, so the top rows by score can
        # hide the few with a confirmed cheap ticket. Tell the model they exist.
        total, priced = con.execute(
            f"SELECT count(*), count(u.min_price) FROM {tables} WHERE {condition}", params
        ).fetchall()[0]
        shows = _rows(
            con,
            f"""SELECT u.event_date, u.local_time, u.city, u.venue, u.event_name, u.artist_name,
                       u.artist_key, u.is_headliner, u.breakout_score, u.size_band,
                       u.lastfm_listeners, u.genres, u.listeners_7d_growth_pct, u.min_price,
                       u.max_price, u.currency, u.ticket_url, u.event_id, u.status,
                       u.min_price IS NOT NULL AS price_known,
                       {LINEUP_COLUMNS.format(t="u.")}
                FROM {tables} {LINEUP_JOIN.format(performers=performers)}
                WHERE {condition}
                ORDER BY u.breakout_score DESC NULLS LAST, u.event_date LIMIT ?""",
            [*params, max_rows or _cap(limit)],
        )
        if live is None:
            notes = ["Prices are CAD. price_known=false means no listed price, not free."]
        else:
            notes = [
                f"Searched live on Ticketmaster for {live['city']}. Prices are in each show's "
                "currency; price_known=false means no listed price, not free.",
                "Outside Toronto and Montréal only artists we already track can appear, so "
                "this is rising artists touring through, not the local scene.",
            ]
        notes += data_notes(con)
    if (until - as_of).days > config.EVENT_HORIZON_DAYS:
        notes.append(f"Shows were collected up to {config.EVENT_HORIZON_DAYS} days after {as_of}.")
    return {
        "as_of_date": _jsonable(as_of),
        "window": {"from": _jsonable(start), "to": _jsonable(until)},
        "filters": filters,
        "count": len(shows),
        "total_matches": total,
        "priced_matches": priced,
        "shows": shows,
        "notes": notes,
    }


def tracked_city_names(con, city: str | None = None) -> list[str]:
    """Snapshot city spellings, all or those matching `city` ("Montreal" ~ "Montréal")."""
    rows = con.execute("SELECT DISTINCT city FROM upcoming_shows").fetchall()
    cities = sorted(c for (c,) in rows if c)
    return cities if city is None else [c for c in cities if _fold(c) == _fold(city)]


# --- 5. freshness -------------------------------------------------------------------------


def snapshot_info(db: str | Path | None = None) -> dict:
    with connect(db) as con:
        meta = _rows(con, "SELECT * FROM snapshot_meta ORDER BY table_name")
        as_of = _as_of(con)
    exported = meta[0]["exported_at"] if meta else None
    return {
        "data_as_of": _jsonable(as_of),
        "exported_at_utc": exported,
        "days_since_data": (_today() - as_of).days if as_of else None,
        "source": meta[0]["source"] if meta else None,
        "row_counts": {m["table_name"]: m["row_count"] for m in meta},
    }
