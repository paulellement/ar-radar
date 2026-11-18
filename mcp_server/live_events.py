"""Shows in any city Ticketmaster sells in, searched live: no pipeline run, no Databricks.

Toronto and Montréal come from the daily snapshot. The pipeline also adds their local
performers to the tracked pool, so even tiny local acts there have breakout scores. Any other
city is one live Ticketmaster search (a handful of requests, cached for an hour), and its
performers are matched to the tracked pool with the pipeline's own matcher: MusicBrainz ID,
then exact name, then strict fuzzy. Only tracked artists can be ranked, so in a new city you
see the rising artists touring through it, not its local scene.

for_city() returns None for the snapshot cities, or the live tables that
ar_data.upcoming_shows(live=...) queries exactly like the snapshot.
"""

import datetime as dt
import logging
import math
import re
import time

import pyarrow as pa
import requests

import ar_data
import tickets
from anr_radar import config
from anr_radar.clients.ticketmaster import TicketmasterClient
from anr_radar.matching.names import MATCHED_METHODS, match_performers
from ar_data import QueryError

log = logging.getLogger("ar-radar.live")

CACHE_TTL_S = 60 * 60
COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")
COVERAGE = (
    "Ticketmaster covers North America, the UK and Ireland, much of Europe, Australia and "
    "New Zealand."
)

EVENTS = pa.schema([
    ("event_id", pa.string()), ("event_name", pa.string()), ("event_date", pa.date32()),
    ("local_time", pa.string()), ("status", pa.string()), ("venue", pa.string()),
    ("city", pa.string()), ("min_price", pa.float64()), ("max_price", pa.float64()),
    ("currency", pa.string()), ("ticket_url", pa.string()),
])  # fmt: skip
PERFORMERS = pa.schema([
    ("event_id", pa.string()), ("billing_order", pa.int64()), ("performer_name", pa.string()),
])  # fmt: skip
MATCHES = pa.schema([
    ("event_id", pa.string()), ("billing_order", pa.int64()), ("artist_key", pa.string()),
    ("artist_name", pa.string()),
])  # fmt: skip

_cache: dict[tuple, tuple[float, dict]] = {}


def search_events(city: str, country: str | None, days: int) -> list[dict]:
    """Raw Discovery API music events in `city` for the next `days` days."""
    key = tickets.api_key()
    if not key:
        raise QueryError(
            "Shows outside Toronto and Montréal need a Ticketmaster API key",
            "Set TICKETMASTER_API_KEY in .env.",
        )
    client = TicketmasterClient(key, requests_per_sec=config.TICKETMASTER_REQUESTS_PER_SEC)
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    try:
        return client.music_events(city, country, now, now + dt.timedelta(days=days))
    except requests.RequestException as e:
        log.warning("live city search failed: %s", e)
        raise QueryError("Ticketmaster search failed", "Try again in a minute.") from e


def _price(e: dict, field: str) -> float | None:
    value = ((e.get("priceRanges") or [{}])[0]).get(field)
    return float(value) if value is not None else None


def _venue(v: dict) -> str | None:
    """Some European listings have no venue name; the street address still says where."""
    return v.get("name") or (v.get("address") or {}).get("line1")


def build(events: list[dict], pool: list[dict]) -> dict:
    """Ticketmaster events -> (events, performers, matches) rows, canceled shows dropped.
    pool: tracked artists as {artist_key, name}."""
    rows, bill = [], []
    for e in events:
        status = e.get("dates", {}).get("status", {}).get("code")
        if status in ("canceled", "cancelled"):
            continue
        start = e.get("dates", {}).get("start", {})
        venue = (e.get("_embedded", {}).get("venues") or [{}])[0]
        try:
            date = dt.date.fromisoformat(start.get("localDate", ""))
        except ValueError:
            continue
        rows.append({
            "event_id": e["id"], "event_name": e.get("name"), "event_date": date,
            "local_time": start.get("localTime"), "status": status, "venue": _venue(venue),
            "city": (venue.get("city") or {}).get("name"), "min_price": _price(e, "min"),
            "max_price": _price(e, "max"), "currency": ((e.get("priceRanges") or [{}])[0]).get("currency"),  # noqa: E501
            "ticket_url": e.get("url"),
        })  # fmt: skip
        for pos, a in enumerate(e.get("_embedded", {}).get("attractions", [])):
            mb = (a.get("externalLinks") or {}).get("musicbrainz") or [{}]
            bill.append({
                "event_id": e["id"], "billing_order": pos, "attraction_id": a.get("id") or a["name"],  # noqa: E501
                "performer_name": a["name"], "performer_mbid": mb[0].get("id") or None,
            })  # fmt: skip
    artists = [
        {**a, "mbid": a["artist_key"][5:] if a["artist_key"].startswith("mbid:") else None}
        for a in pool
    ]
    unique = list({p["attraction_id"]: p for p in bill}.values())
    found = {
        m["attraction_id"]: m
        for m in match_performers(unique, artists)
        if m["match_method"] in MATCHED_METHODS
    }
    matches = [
        {"event_id": p["event_id"], "billing_order": p["billing_order"],
         "artist_key": found[p["attraction_id"]]["artist_key"],
         "artist_name": found[p["attraction_id"]]["artist_name"]}
        for p in bill if p["attraction_id"] in found
    ]  # fmt: skip
    return {
        "events": pa.Table.from_pylist(rows, schema=EVENTS),
        "performers": pa.Table.from_pylist(bill, schema=PERFORMERS),
        "matches": pa.Table.from_pylist(matches, schema=MATCHES),
    }


def for_city(
    city: str | None,
    country_code: str | None = None,
    days_ahead: int = 30,
    db=None,
    search=None,
) -> dict | None:
    """None if the snapshot covers `city` (or no city was given); otherwise live tables for
    ar_data.upcoming_shows(live=...). Cached per city for CACHE_TTL_S."""
    if not city or not city.strip():
        return None
    city = city.strip()
    with ar_data.connect(db) as con:
        if ar_data.tracked_city_names(con, city):
            return None
        pool = ar_data._rows(con, "SELECT artist_key, name FROM artist_signals")
    if country_code and not COUNTRY_CODE.match(country_code.strip()):
        raise QueryError(
            f"Unknown country code {country_code!r}", "Use a two-letter code like GB or US."
        )
    country = country_code.strip().upper() if country_code else None
    # Fetch whole 30-day blocks so moving a "days ahead" slider reuses the cache.
    days = min(config.EVENT_HORIZON_DAYS, 30 * math.ceil(days_ahead / 30))
    key = (city.casefold(), country, days)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    events = (search or search_events)(city, country, days)
    if not events:
        raise QueryError(
            f"Ticketmaster lists no music events in {city!r}",
            f"Check the spelling, or add a country code. {COVERAGE}",
        )
    tickets.remember(events)  # ticket status comes free with the search
    live = build(events, pool) | {"city": city, "events_found": len(events)}
    log.info("live search: %d events, %d matched performers", len(events), live["matches"].num_rows)  # noqa: E501
    _cache[key] = (time.monotonic(), live)
    return live
