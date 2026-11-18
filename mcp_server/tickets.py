"""Live ticket status from Ticketmaster, checked when answering (statuses change faster than the
daily snapshot). One batched Discovery API request per answer, cached for a few minutes.

Ticketmaster's public API has no "sold out" field: dates.status.code is one of onsale, offsale,
canceled, postponed, rescheduled, and "offsale" isn't defined. (Real availability lives in the
partner-only Inventory Status API, which our key can't use.) So we report what the sale window
implies and never claim "sold out" as fact:
  offsale before public sales open  -> not on sale yet
  offsale while sales should be open -> off sale (often sold out, unconfirmed)
  offsale after sales closed         -> sales closed
Canceled shows are removed from answers. If Ticketmaster can't be reached, status is "unknown".
"""

import datetime as dt
import logging
import time

import requests

from anr_radar.secrets import get_secret

log = logging.getLogger("ar-radar.tickets")

EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
CACHE_TTL_S = 10 * 60
BATCH = 50
_cache: dict[str, tuple[float, dict | None]] = {}


def api_key() -> str | None:
    """Ticketmaster key from .env or the secret scope; None means statuses come back unknown."""
    try:
        return get_secret("TICKETMASTER_API_KEY", "ticketmaster_key")
    except RuntimeError:
        return None


def fetch_events(api_key: str, event_ids: list[str]) -> dict[str, dict]:
    """event_id -> Discovery API event, in batches of BATCH ids per request."""
    found: dict[str, dict] = {}
    for i in range(0, len(event_ids), BATCH):
        chunk = event_ids[i : i + BATCH]
        r = requests.get(
            EVENTS_URL,
            params={"id": ",".join(chunk), "size": len(chunk), "apikey": api_key},
            timeout=15,
        )
        r.raise_for_status()
        for e in r.json().get("_embedded", {}).get("events", []):
            found[e["id"]] = e
    return found


def remember(events: list[dict]) -> None:
    """Seed the status cache with full events fetched elsewhere (a live city search)."""
    now = time.monotonic()
    for e in events:
        _cache[e["id"]] = (now, e)


def _when(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def classify(event: dict | None, now: dt.datetime) -> tuple[str, str | None]:
    """(ticket_status, note) for one Discovery API event (None = not returned)."""
    if event is None:
        return "unknown", "Not found on Ticketmaster any more (removed or canceled?)."
    code = event.get("dates", {}).get("status", {}).get("code", "")
    sale = event.get("sales", {}).get("public", {})
    start, end = _when(sale.get("startDateTime")), _when(sale.get("endDateTime"))
    new_date = event.get("dates", {}).get("start", {}).get("localDate")
    if code == "onsale":
        return "on sale", None
    if code in ("canceled", "cancelled"):
        return "canceled", "Ticketmaster lists this show as canceled."
    if code in ("postponed", "rescheduled"):
        return code, f"Ticketmaster lists it as {code}" + (
            f"; date now {new_date}." if new_date else "."
        )
    if code == "offsale":
        if start and now < start:
            return "not on sale yet", f"Public sale starts {start.date()}."
        if end and now < end:
            return (
                "off sale",
                "Not selling during its sale window: often sold out, but Ticketmaster "
                "doesn't say. Check the link.",
            )
        return "sales closed", None
    return "unknown", None


def annotate(result: dict, api_key: str | None, fetch=None, key: str = "shows") -> dict:
    """Add ticket_status / ticket_note to result[key] (a list of shows with event_id), drop
    canceled shows, and say so in result["notes"]. Never raises: no key or no network just
    means status "unknown"."""
    fetch = fetch or fetch_events  # looked up at call time, so tests can swap it
    shows = result.get(key) or []
    ids = list(dict.fromkeys(s["event_id"] for s in shows if s.get("event_id")))
    now = dt.datetime.now(dt.UTC)
    fresh = {i for i in ids if i in _cache and time.monotonic() - _cache[i][0] < CACHE_TTL_S}
    missing = [i for i in ids if i not in fresh]
    checked = True
    if missing and api_key:
        try:
            found = fetch(api_key, missing)
            for i in missing:
                _cache[i] = (time.monotonic(), found.get(i))
        except Exception as e:  # a status label must never break the answer
            log.warning("live ticket status unavailable: %s", e)
            checked = False
    elif missing:
        checked = False

    kept, canceled = [], 0
    for s in shows:
        if checked or s.get("event_id") in fresh:
            status, note = classify(_cache.get(s.get("event_id"), (0, None))[1], now)
        else:
            status, note = "unknown", "Couldn't check live ticket status."
        if status == "canceled":
            canceled += 1
            continue
        s["ticket_status"], s["ticket_note"] = status, note
        kept.append(s)
    result[key] = kept
    if "count" in result:
        result["count"] = len(kept)
    notes = result.setdefault("notes", [])
    notes.append("ticket_status is live from Ticketmaster; 'off sale' is not a confirmed sell-out.")  # noqa: E501
    if canceled:
        notes.append(f"Removed {canceled} show(s) Ticketmaster now lists as canceled.")
    return result
