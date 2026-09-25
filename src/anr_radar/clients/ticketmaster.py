"""Ticketmaster Discovery API client: music events by city, with rate limiting and retries."""

import datetime as dt
import logging
import time

import requests

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200
MAX_RESULTS = 1000  # deep-paging limit: page * size must stay under 1000

log = logging.getLogger(__name__)


def _ts(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TicketmasterClient:
    def __init__(
        self,
        api_key: str,
        requests_per_sec: float = 4.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key
        self.min_interval = 1.0 / requests_per_sec
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last_call = 0.0

    def _get(self, **params) -> dict:
        for attempt in range(self.max_retries + 1):
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(
                    API_URL, params={"apikey": self.api_key, **params}, timeout=30
                )
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("ticketmaster request failed (%s), retrying", exc)
                time.sleep(2**attempt)
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise AssertionError("unreachable")

    def _page(self, city, country, start, end, page) -> dict:
        return self._get(
            classificationName="music",
            city=city,
            countryCode=country,
            startDateTime=_ts(start),
            endDateTime=_ts(end),
            size=PAGE_SIZE,
            page=page,
            sort="date,asc",
        )

    def music_events(
        self, city: str, country: str, start: dt.datetime, end: dt.datetime
    ) -> list[dict]:
        """All music events in [start, end). Windows with more results than the deep-paging
        limit are split in half until each fits."""
        first = self._page(city, country, start, end, 0)
        total = first.get("page", {}).get("totalElements", 0)
        if total > MAX_RESULTS and end - start > dt.timedelta(hours=2):
            mid = start + (end - start) / 2
            return self.music_events(city, country, start, mid) + self.music_events(
                city, country, mid, end
            )

        events = first.get("_embedded", {}).get("events", [])
        pages = min(first.get("page", {}).get("totalPages", 1), MAX_RESULTS // PAGE_SIZE)
        for page in range(1, pages):
            events += (
                self._page(city, country, start, end, page).get("_embedded", {}).get("events", [])
            )
        return events
