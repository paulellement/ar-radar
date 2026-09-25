"""Match event performers (Ticketmaster attractions) to tracked artists.

Order of evidence: shared MusicBrainz ID, then identical normalized name, then fuzzy name
similarity. Fuzzy matches below AUTO_THRESHOLD go to a review queue instead of being
trusted, and tribute acts are never auto-matched to the artist they cover.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz, process

AUTO_THRESHOLD = 95
REVIEW_THRESHOLD = 85
MIN_FUZZY_LEN = 5  # short names ("AJJ" vs "AJR") only ever match exactly

TRIBUTE = re.compile(
    r"\b(tribute|salute to|the music of|celebrating the music|songs of|a night of)\b",
    re.IGNORECASE,
)

MATCHED_METHODS = ("mbid", "exact", "fuzzy")


def normalize(name: str) -> str:
    """Casefold, strip accents, '&' -> 'and', drop a leading 'the' and punctuation."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.removeprefix("the ").strip() or s


@dataclass(frozen=True)
class Match:
    attraction_id: str
    performer_name: str
    artist_key: str | None
    artist_name: str | None
    match_score: float
    match_method: str  # mbid | exact | fuzzy | review | tribute | none


def _best_match(p: dict, by_mbid: dict, by_name: dict) -> tuple[dict | None, float, str]:
    """(artist or None, score, method) for one performer."""
    name, mbid = p["performer_name"], (p.get("performer_mbid") or "").lower()
    if TRIBUTE.search(name):
        return None, 0, "tribute"
    if mbid in by_mbid:
        return by_mbid[mbid], 100, "mbid"
    norm = normalize(name)
    if norm in by_name:
        return by_name[norm], 100, "exact"
    best = process.extractOne(
        norm, list(by_name), scorer=fuzz.token_sort_ratio, score_cutoff=REVIEW_THRESHOLD
    )
    if best is None:
        return None, 0, "none"
    choice, score, _ = best
    trusted = score >= AUTO_THRESHOLD and len(norm) >= MIN_FUZZY_LEN
    return by_name[choice], score, "fuzzy" if trusted else "review"


def match_performers(performers: list[dict], artists: list[dict]) -> list[dict]:
    """performers: attraction_id, performer_name, performer_mbid
    artists:    artist_key, name, mbid"""
    by_mbid = {a["mbid"].lower(): a for a in artists if a.get("mbid")}
    by_name: dict[str, dict] = {}
    for a in artists:
        by_name.setdefault(normalize(a["name"]), a)

    out = []
    for p in performers:
        artist, score, method = _best_match(p, by_mbid, by_name)
        out.append(
            asdict(
                Match(
                    attraction_id=p["attraction_id"],
                    performer_name=p["performer_name"],
                    artist_key=artist["artist_key"] if artist else None,
                    artist_name=artist["name"] if artist else None,
                    match_score=float(score),
                    match_method=method,
                )
            )
        )
    return out
