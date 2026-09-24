"""Thin Last.fm API client with rate limiting and retries."""

import logging
import time

import requests

API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "anr-radar/0.1 (personal portfolio project)"

# https://www.last.fm/api/errorcodes
RETRYABLE_ERRORS = {8, 11, 16, 29}  # operation failed, offline, temporary, rate limited
NOT_FOUND_ERROR = 6

log = logging.getLogger(__name__)


class LastFmError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"Last.fm error {code}: {message}")
        self.code = code


class NotFound(LastFmError):
    pass


class LastFmClient:
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
        self.session.headers["User-Agent"] = USER_AGENT
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def call(self, method: str, **params) -> dict:
        query = {"method": method, "api_key": self.api_key, "format": "json", **params}
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(API_URL, params=query, timeout=20)
                data = resp.json() if resp.content else {}
            except (requests.RequestException, ValueError) as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("%s failed (%s), retrying", method, exc)
                time.sleep(2**attempt)
                continue

            if "error" in data:
                code, message = data["error"], data.get("message", "")
                if code == NOT_FOUND_ERROR:
                    raise NotFound(code, message)
                if code in RETRYABLE_ERRORS and attempt < self.max_retries:
                    log.warning("%s: error %s (%s), retrying", method, code, message)
                    time.sleep(2**attempt)
                    continue
                raise LastFmError(code, message)

            if resp.status_code >= 500 and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return data
        raise AssertionError("unreachable")

    # --- endpoints -------------------------------------------------------

    def geo_top_artists(self, country: str, limit: int = 100, page: int = 1) -> list[dict]:
        data = self.call("geo.gettopartists", country=country, limit=limit, page=page)
        return _as_list(data.get("topartists", {}).get("artist"))

    def tag_top_artists(self, tag: str, limit: int = 100, page: int = 1) -> list[dict]:
        data = self.call("tag.gettopartists", tag=tag, limit=limit, page=page)
        return _as_list(data.get("topartists", {}).get("artist"))

    def similar_artists(self, name: str, limit: int = 15) -> list[dict]:
        data = self.call("artist.getsimilar", artist=name, limit=limit, autocorrect=0)
        return _as_list(data.get("similarartists", {}).get("artist"))

    def artist_info(self, name: str) -> dict:
        # Look up by name: Last.fm's mbid lookups fail for many artists that do have a name page.
        data = self.call("artist.getinfo", artist=name, autocorrect=0)
        return data["artist"]


def _as_list(value) -> list[dict]:
    """Last.fm returns a bare object instead of a one-element list for single results."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
