"""Stable artist identifiers."""

import re
import unicodedata


def normalize_name(name: str) -> str:
    """Casefold, strip accents and collapse whitespace so name variants share a key."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def artist_key(name: str, mbid: str | None) -> str:
    """MusicBrainz ID when Last.fm provides one, otherwise a name-based key."""
    if mbid:
        return f"mbid:{mbid.lower()}"
    return f"name:{normalize_name(name)}"
