"""Personal answers from a Last.fm username. Plain functions, no MCP code (like ar_data.py).

A user's listening is public by username, so no login is needed. It is fetched live, kept in
memory for CACHE_TTL_S, and never written to disk, the snapshot, or the logs (logs carry a short
hash instead of the username).

Affinity of a tracked artist to a user, 0-1, explainable by construction:
    0.7 * similar-artist score  (sum of seed weight x Last.fm "similar" match, scaled by the max)
  + 0.3 * tag score             (cosine of the user's weighted tags vs. the artist's top tags)
Seeds are the user's top artists, weighted by log play counts (all-time + last 3 months).
"""

import hashlib
import logging
import math
import re
import time
from collections import defaultdict

import ar_data
from anr_radar import config
from anr_radar.clients.lastfm import LastFmClient, LastFmError, NotFound
from anr_radar.keys import artist_key
from anr_radar.matching.names import normalize
from anr_radar.secrets import get_secret
from ar_data import QueryError

log = logging.getLogger("ar-radar.personal")

CACHE_TTL_S = 30 * 60
TOP_N = 50  # seeds: the top artists that define taste weights
LIBRARY_N = 500  # "artists you listen to": all-time top 500 (one API call)
LIVE_SIMILAR_SEEDS = 10  # heavy seeds we don't track: ask Last.fm for their similar artists
RISING = 70.0
SIM_WEIGHT, TAG_WEIGHT = 0.7, 0.3
EMERGING_BANDS = {"<10k", "10k-50k", "50k-500k"}
# "Your artists breaking": an artist with 1M+ listeners is established, however fast they grow.
# Below that, smaller ranks higher on a log scale (SMALL_FLOOR or fewer listeners = 1).
ESTABLISHED, SMALL_FLOOR = 1_000_000, 10_000
JUNK_TAGS = {"seen live", "favorites", "favourites", "favorite", "fav", "love", "albums i own"}
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,14}$")  # Last.fm's username rules

_cache: dict[tuple, tuple[float, object]] = {}
_client: LastFmClient | None = None


def client() -> LastFmClient:
    """The shared Last.fm client (MCP server and web app). Key from .env or the secret scope."""
    global _client
    if _client is None:
        try:
            key = get_secret("LASTFM_API_KEY", "lastfm_key")
        except RuntimeError as e:
            raise QueryError("Last.fm API key not configured", "Set LASTFM_API_KEY in .env.") from e
        _client = LastFmClient(key, requests_per_sec=config.LASTFM_REQUESTS_PER_SEC)
    return _client


def _cached(key: tuple, fetch):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = fetch()
    _cache[key] = (time.monotonic(), value)
    return value


def _tag(username: str) -> str:
    """What the logs show instead of a username."""
    return "user#" + hashlib.sha256(username.lower().encode()).hexdigest()[:8]


# --- fetching and mapping -------------------------------------------------------------------


def fetch_listening(client: LastFmClient, username: str) -> dict:
    if not USERNAME.match(username or ""):
        raise QueryError(f"{username!r} isn't a valid Last.fm username", "Ask the user for it.")

    def fetch() -> dict:
        log.info("fetching listening for %s", _tag(username))
        try:
            info = client.user_info(username)
            overall = client.user_top_artists(username, "overall", LIBRARY_N)
            recent = client.user_top_artists(username, "3month", TOP_N)
        except NotFound as e:
            raise QueryError(f"No Last.fm user {username!r}", "Check the spelling.") from e
        except LastFmError as e:
            raise QueryError(
                f"Last.fm refused the request ({e})", "The profile may be private."
            ) from e
        return {"scrobbles": int(info.get("playcount") or 0), "overall": overall, "recent": recent}

    return _cached(("listening", username.lower()), fetch)


def seeds_from(listening: dict) -> list[dict]:
    """Merge all-time and recent top artists; recent plays count again (current taste)."""
    merged: dict[str, dict] = {}
    for period in ("overall", "recent"):
        for a in listening[period]:
            s = merged.setdefault(
                a["name"].lower(),
                {"name": a["name"], "mbid": a.get("mbid"), "plays": 0, "weight": 0.0},
            )
            plays = int(a.get("playcount") or 0)
            s["weight"] += math.log1p(plays)
            if period == "overall":
                s["plays"] = plays
    seeds = sorted(merged.values(), key=lambda s: -s["weight"])[:TOP_N]
    total = sum(s["weight"] for s in seeds) or 1.0
    for s in seeds:
        s["weight"] /= total
    return seeds


def load_pool(con) -> tuple[dict, dict]:
    """(rows by artist_key, artist_key by normalized name), preferring real pages over aliases."""
    rows = ar_data._rows(
        con,
        """SELECT artist_key, name, tags, genres, size_band, breakout_score, lastfm_listeners,
                  listeners_7d_growth_pct, is_provisional, possible_alias_of
           FROM artist_signals ORDER BY possible_alias_of NULLS FIRST, lastfm_listeners DESC""",
    )
    by_key = {r["artist_key"]: r for r in rows}
    by_name: dict[str, str] = {}
    for r in rows:
        by_name.setdefault(normalize(r["name"]), r["artist_key"])
    return by_key, by_name


def map_artist(name: str, mbid: str | None, by_key: dict, by_name: dict) -> str | None:
    """MusicBrainz ID, then exact normalized name. Deliberately no fuzzy step: a wrong merge
    (Spoon vs Spoons) would become a confident wrong recommendation."""
    key = artist_key(name, mbid or None)
    if key in by_key:
        return key
    return by_name.get(normalize(name))


# --- affinity -------------------------------------------------------------------------------


def _tag_vector(tags: list[str] | None, top: int = 5) -> dict[str, float]:
    vec: dict[str, float] = {}
    for i, t in enumerate(t for t in (tags or []) if t.lower() not in JUNK_TAGS):
        if i >= top:
            break
        vec[t.lower()] = vec.get(t.lower(), 0) + 1 / (i + 1)
    return vec


def _cosine(a: dict, b: dict) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


class Taste:
    """Everything personal for one request: seeds mapped to the pool, and an affinity scorer."""

    def __init__(self, client: LastFmClient, username: str, con):
        self.listening = fetch_listening(client, username)
        self.seeds = seeds_from(self.listening)
        self.by_key, by_name = load_pool(con)
        for s in self.seeds:
            s["artist_key"] = map_artist(s["name"], s["mbid"], self.by_key, by_name)
        # Your library: every tracked artist in your all-time top 500 (+ recent top 50), with
        # plays. It's not a yes/no "yours" list: familiarity() turns plays into a 0-1 dial, so
        # one play in passing counts as almost new and a favourite as fully yours.
        self.library: dict[str, int] = {}
        for a in self.listening["overall"] + self.listening["recent"]:
            key = map_artist(a["name"], a.get("mbid"), self.by_key, by_name)
            if key and key not in self.library:  # all-time plays win over 3-month plays
                self.library[key] = int(a.get("playcount") or 0)
        self.known = set(self.library)
        self.top_plays = math.log1p(max(self.library.values(), default=0)) or 1.0
        self.tags: dict[str, float] = defaultdict(float)
        for s in self.seeds:
            if s["artist_key"]:
                for t, v in _tag_vector(self.by_key[s["artist_key"]]["tags"], 10).items():
                    self.tags[t] += s["weight"] * v
        self.similar = self._similar_scores(client, con, by_name)
        # Scale similarity so the best-connected candidate scores 1.
        self.top_similar = max((sum(c.values()) for c in self.similar.values()), default=0) or 1

    def _similar_scores(self, client, con, by_name) -> dict[str, dict[str, float]]:
        """candidate key -> {seed name: weight x match}. Snapshot edges count both ways."""
        out: dict[str, dict[str, float]] = defaultdict(dict)
        seed_by_key = {s["artist_key"]: s for s in self.seeds if s["artist_key"]}
        keys = list(seed_by_key)
        for frm, to, match in con.execute(
            "SELECT from_key, to_key, match FROM artist_similar "
            "WHERE list_contains(?, from_key) OR list_contains(?, to_key)",
            [keys, keys],
        ).fetchall():
            for seed_key, cand in ((frm, to), (to, frm)):
                # Edges can point to artists we don't track: they can't be recommended.
                if seed_key in seed_by_key and cand in self.by_key and cand not in seed_by_key:
                    s = seed_by_key[seed_key]
                    out[cand][s["name"]] = max(out[cand].get(s["name"], 0), s["weight"] * match)
        untracked = [s for s in self.seeds if not s["artist_key"]][:LIVE_SIMILAR_SEEDS]
        for s in untracked:
            similar = _cached(
                ("similar", s["name"].lower()), lambda n=s["name"]: _live_similar(client, n)
            )
            for a in similar:
                cand = map_artist(a["name"], a.get("mbid"), self.by_key, by_name)
                if cand and cand not in seed_by_key:
                    w = s["weight"] * float(a.get("match") or 0)
                    out[cand][s["name"]] = max(out[cand].get(s["name"], 0), w)
        return out

    def affinity(self, key: str) -> tuple[float, str]:
        """(0-1 score, 'because' text) for one tracked artist."""
        contributions = self.similar.get(key, {})
        sim = sum(contributions.values()) / self.top_similar
        cand_tags = _tag_vector(self.by_key[key]["tags"])
        tag = _cosine(dict(self.tags), cand_tags)
        reasons = []
        seeds = sorted(contributions.items(), key=lambda kv: -kv[1])[:2]
        if seeds:
            reasons.append("similar to " + ", ".join(name for name, _ in seeds))
        shared = sorted(set(cand_tags) & set(self.tags), key=lambda t: -self.tags[t])[:3]
        if shared:
            reasons.append("shares " + ", ".join(shared))
        weight = SIM_WEIGHT if self.similar else 0.0  # cold start: tags only
        return round(weight * sim + (1 - weight) * tag, 3), "; ".join(reasons)

    def familiarity(self, key: str) -> float:
        """0-1: your plays, log-scaled against your most-played artist. 1 play ~0.1."""
        return math.log1p(self.library.get(key, 0)) / self.top_plays

    def explain(self, key: str) -> tuple[float, str]:
        """(affinity, why): plays (if any) plus the similar artists / tags behind the match."""
        affinity, because = self.affinity(key)
        n = self.library.get(key, 0)
        played = f"you've played them {n} time{'s' if n != 1 else ''}" if n else ""
        return affinity, "; ".join(p for p in (played, because) if p)

    def coverage(self) -> float:
        return round(sum(1 for s in self.seeds if s["artist_key"]) / len(self.seeds), 3)

    def notes(self) -> list[str]:
        notes = []
        if not self.similar:
            notes.append(
                "None of your top artists link to tracked artists; ranking uses tags only."
            )
        if self.coverage() < 0.2:
            notes.append(
                f"Only {self.coverage():.0%} of your top artists are tracked, so matches are rough."
            )
        return notes


def _live_similar(client: LastFmClient, name: str) -> list[dict]:
    try:
        return client.similar_artists(name, limit=30)
    except LastFmError:
        return []  # one missing artist shouldn't sink the whole answer


def _taste(client, username, con) -> Taste:
    taste = Taste(client, username, con)
    if not taste.seeds:
        raise QueryError(
            f"{username} has no listening history on Last.fm", "Nothing to personalize from yet."
        )
    return taste


# --- the three answers ------------------------------------------------------------------------


def taste_profile(client: LastFmClient, username: str, db=None) -> dict:
    with ar_data.connect(db) as con:
        taste, as_of = _taste(client, username, con), ar_data._as_of(con)
        notes = taste.notes() + ar_data.data_notes(con)
    tracked = [s for s in taste.seeds if s["artist_key"]]
    emerging = sum(
        s["weight"] for s in tracked if taste.by_key[s["artist_key"]]["size_band"] in EMERGING_BANDS
    )
    rising = [
        {**_artist(taste.by_key[key]), "your_playcount": plays}
        for key, plays in taste.library.items()
        if (taste.by_key[key]["breakout_score"] or 0) >= RISING
        and not taste.by_key[key]["possible_alias_of"]
    ]
    # Growing fast for their size isn't "breaking" once an artist has 1M+ listeners: rank the
    # rest by breakout x smallness x how much they're yours. One play in passing still counts
    # a quarter, so a small artist you only just found isn't buried, but a favourite leads.
    for a in rising:
        fam = taste.familiarity(a["artist_key"])
        a["rank_score"] = round(
            a["breakout_score"] / 100 * smallness(a["lastfm_listeners"]) * (0.25 + 0.75 * fam), 3
        )
    breaking = sorted((a for a in rising if a["rank_score"] > 0), key=lambda a: -a["rank_score"])
    if len(rising) > len(breaking):
        notes.append(
            f"{len(rising) - len(breaking)} more of your artists score >= {RISING:.0f} but "
            f"already have {ESTABLISHED / 1e6:.0f}M+ listeners; left out as established."
        )
    return {
        "username": username,
        "as_of_date": ar_data._jsonable(as_of),
        "total_scrobbles": taste.listening["scrobbles"],
        "top_artists": [
            {
                "name": s["name"],
                "artist_key": s["artist_key"],
                "your_playcount": s["plays"],
                "tracked": bool(s["artist_key"]),
                "size_band": taste.by_key[s["artist_key"]]["size_band"]
                if s["artist_key"]
                else None,
            }
            for s in taste.seeds[:10]
        ],
        "top_tags": [t for t, _ in sorted(taste.tags.items(), key=lambda kv: -kv[1])[:10]],
        "coverage": taste.coverage(),
        "emerging_share": round(emerging / (sum(s["weight"] for s in tracked) or 1), 3),
        "library_tracked": len(taste.library),
        "your_artists_breaking": breaking[:20],
        "notes": notes,
    }


def discover(
    client, username, genre=None, size_band=None, min_breakout_score=RISING, limit=10, db=None
) -> dict:
    if not 0 <= min_breakout_score <= 100:
        raise QueryError("min_breakout_score must be 0-100", "70+ is a common 'rising' cut-off.")
    band = ar_data.resolve_size_band(size_band) if size_band else None
    with ar_data.connect(db) as con:
        taste, as_of = _taste(client, username, con), ar_data._as_of(con)
        notes = taste.notes() + ar_data.data_notes(con)
    picks = []
    for key, a in taste.by_key.items():
        if (
            a["possible_alias_of"]
            or (a["breakout_score"] or 0) < min_breakout_score
            or (band and a["size_band"] != band)
            or (not band and a["size_band"] == ar_data.SUPERSTAR_BAND)
            or (genre and not any(genre.lower() in t.lower() for t in a["tags"] or []))
        ):
            continue
        # Soft, not a library cut-off: once-played artists keep ~80% of their score, favourites
        # drop to ~0. "Don't listen to yet" is a matter of degree.
        novelty = (1 - taste.familiarity(key)) ** 2
        affinity, why = taste.explain(key)
        rank = affinity * a["breakout_score"] / 100 * novelty
        if rank > 0:
            picks.append(
                {**_artist(a), "affinity": affinity, "because": why,
                 "your_plays": taste.library.get(key, 0), "rank_score": round(rank, 3)}
            )  # fmt: skip
    picks.sort(key=lambda p: -p["rank_score"])
    return {
        "username": username,
        "as_of_date": ar_data._jsonable(as_of),
        "count": min(len(picks), ar_data._cap(limit)),
        "artists": picks[: ar_data._cap(limit)],
        "notes": notes,
    }


# How shows_for_me ranks. Every factor is 0-1 and returned per show in `score_parts`:
#   familiarity  your plays, log-scaled (1 play ~0.1, your most-played = 1); novelty = 1 - it
#   match        taste affinity (similar artists + tags)
#   connection   max(familiarity, match): you play them, or they fit your taste
#   breakout     breakout score / 100
#   emerging     1 if under 500k listeners: the small-room, cheaper-ticket case
#   cheap        1 - price / CHEAP_SCALE, for a listed non-zero price; else 0
# No hard "yours" vs "new" groups: `who` only shifts weight between familiarity and novelty, so
# an artist you played once ranks almost like a new one. The model maps the user's wording to
# who= / prefer_cheap=; the weights stay fixed here.
SHOW_WEIGHTS = {
    "any": {"connection": 0.4, "breakout": 0.4, "emerging": 0.2},
    "mine": {"familiarity": 0.55, "breakout": 0.25, "emerging": 0.2},
    "new": {"match": 0.35, "novelty": 0.25, "breakout": 0.25, "emerging": 0.15},
}
SUPPORT_SLOT = 0.8  # multiplier for an opening slot: it's mainly the headliner's show
CHEAP_WEIGHT = 0.35  # prefer_cheap: the weights above x (1 - 0.35), plus cheap x 0.35
CHEAP_SCALE = 100.0  # CAD: $25 -> 0.75, $60 -> 0.4, $100+ -> 0
WHO = tuple(SHOW_WEIGHTS)


def cheapness(price: float | None) -> float:
    """Only a listed, non-zero price earns credit: missing isn't $0, and Ticketmaster's $0 is
    often a placeholder."""
    return max(0.0, 1 - price / CHEAP_SCALE) if price else 0.0


def show_weights(who: str, prefer_cheap: bool) -> dict[str, float]:
    base = SHOW_WEIGHTS[who]
    if not prefer_cheap:
        return base
    return {f: round(w * (1 - CHEAP_WEIGHT), 4) for f, w in base.items()} | {"cheap": CHEAP_WEIGHT}


def shows_for_me(
    client,
    username,
    city=None,
    max_price=None,
    days_ahead=30,
    who="any",
    prefer_cheap=False,
    limit=10,
    db=None,
    live=None,
) -> dict:
    """live: a live city search (live_events.for_city) for cities outside the snapshot."""
    if who not in WHO:
        raise QueryError(
            f"who must be one of {', '.join(WHO)}", "'mine' leans on artists they play."
        )  # noqa: E501
    with ar_data.connect(db) as con:
        taste = _taste(client, username, con)
    # Candidates: artists you've played at all (even once) or that Last.fm links to your
    # artists as similar. Not tag-only matches: sharing "pop" or "indie" with your profile
    # flooded the list with rising acts that had almost nothing to do with you.
    result = ar_data.upcoming_shows(
        city=city, max_price=max_price, days_ahead=days_ahead, max_rows=5000, db=db, live=live
    )
    weights = show_weights(who, prefer_cheap)
    shows = []
    for show in result["shows"]:
        key = show["artist_key"]
        if key not in taste.known and key not in taste.similar:
            continue
        match, why = taste.explain(key)
        familiarity = taste.familiarity(key)
        parts = {
            "familiarity": familiarity,
            "novelty": 1 - familiarity,
            "match": match,
            "connection": max(familiarity, match),
            "breakout": (show["breakout_score"] or 0) / 100,
            "emerging": 1.0 if show["size_band"] in EMERGING_BANDS else 0.0,
            "cheap": cheapness(show["min_price"]),
        }
        billing = 1.0 if show["role"] == "headliner" else SUPPORT_SLOT
        if show["role"] == "support" and show["headliner"]:
            why = f"opening for {show['headliner']}" + (f"; {why}" if why else "")
        show["why"] = why
        score = sum(w * parts[f] for f, w in weights.items()) * billing
        show["rank_score"] = round(score, 3)
        show["score_parts"] = {f: round(parts[f], 3) for f in weights} | {"billing": billing}
        shows.append(show)
    shows.sort(key=lambda s: (-s["rank_score"], s["event_date"]))
    result["total_matches"] = len(shows)
    result["priced_matches"] = sum(s["price_known"] for s in shows)
    result["shows"] = shows[: ar_data._cap(limit)]
    result["count"] = len(result["shows"])
    result["filters"] |= {"who": who, "prefer_cheap": prefer_cheap}
    result["username"] = username
    result["notes"] = taste.notes() + result["notes"]
    return result


def smallness(listeners: int | None) -> float:
    """0-1, log scale: SMALL_FLOOR listeners or fewer = 1, ESTABLISHED or more = 0."""
    if not listeners:
        return 0.0
    span = math.log(ESTABLISHED / SMALL_FLOOR)
    return min(1.0, max(0.0, math.log(ESTABLISHED / listeners) / span))


def _artist(a: dict) -> dict:
    return {
        k: a[k]
        for k in (
            "name",
            "artist_key",
            "breakout_score",
            "size_band",
            "lastfm_listeners",
            "listeners_7d_growth_pct",
            "genres",
            "is_provisional",
        )
    }
