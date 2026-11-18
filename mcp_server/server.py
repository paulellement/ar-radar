"""A&R Radar MCP server: read-only scouting tools over a local DuckDB snapshot.

    python mcp_server/server.py                 # stdio (what Claude Code / Desktop launch)
    python mcp_server/server.py --http          # streamable HTTP on 127.0.0.1:8000/mcp

With stdio, the client runs this file as a subprocess and speaks JSON-RPC over stdin/stdout,
so nothing may print to stdout: all logging goes to stderr.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import ar_data
import personal
import tickets
from anr_radar import config
from anr_radar.clients.lastfm import LastFmClient
from anr_radar.secrets import get_secret

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ar-radar")
CONTEXT = Path(__file__).parent / "context"

mcp = MCPServer(
    name="ar-radar",
    instructions=(
        "A&R Radar: artists growing unusually fast for their size (Last.fm) and their upcoming "
        "Toronto/Montreal shows (Ticketmaster). Data is a daily snapshot: call data_status "
        "before saying 'today' or 'this week'. It flags growth; never say an artist will break. "
        "Read ar-radar://limitations before writing a report. Fields ending in _pct are "
        "already percentages: 0.21 means +0.21%, not +21%. Personal tools (my_*, *_for_me) "
        "read the user's public Last.fm listening; only look up the user's own username."
    ),
)
# Every tool only reads: tell clients so they can skip confirmation prompts. Tools that only
# read the snapshot are closed-world; tools that also call Last.fm or Ticketmaster live
# (personal taste, ticket status) are marked open-world.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
READS_LIVE = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
_lastfm: LastFmClient | None = None
_ticket_fetch = tickets.fetch_events  # swapped out in tests: no network


def _with_tickets(result: dict[str, Any], key: str = "shows") -> dict[str, Any]:
    """Add live ticket status (on sale / off sale / ...) to every show; drop canceled ones."""
    if "error" in result or not result.get(key):
        return result
    try:
        api_key = get_secret("TICKETMASTER_API_KEY", "ticketmaster_key")
    except RuntimeError:
        api_key = None  # shows still come back, marked "unknown"
    return tickets.annotate(result, api_key, fetch=_ticket_fetch, key=key)


def _call(fn, **kwargs) -> dict[str, Any]:
    """Bad input comes back as {"error", "hint"} the model can act on, not a stack trace."""
    try:
        return fn(**kwargs)
    except ar_data.QueryError as e:
        return {"error": str(e), "hint": e.hint}
    except Exception:
        log.exception("%s failed", fn.__name__)
        return {"error": "Internal error reading the snapshot", "hint": "Check the server log."}


@mcp.tool(annotations=READ_ONLY)
def top_breakout_artists(
    country: str | None = None,
    genre: str | None = None,
    size_band: str | None = None,
    include_superstars: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank artists by breakout score: how fast they're growing compared with artists of the
    same size (0-100 percentile; 90+ = top 10% for their size). Use for "who's breaking /
    rising / trending / blowing up".

    Args:
        country: country name or ISO code (e.g. "Canada", "CA", "Brazil"). Matches artists in
            that country's Last.fm top-100 today or with a nationality tag ("canadian");
            each row says which via country_evidence.
        genre: a Last.fm tag such as "indie pop", "hip-hop", "shoegaze" (substring match).
        size_band: one of "<10k", "10k-50k", "50k-500k", "500k-2M", "2M+" (all-time
            listeners). "Emerging"/"small" usually means the first three.
        include_superstars: include 2M+ artists (excluded by default).
        limit: rows to return, max 50.
    """
    return _call(
        ar_data.top_breakout_artists,
        country=country,
        genre=genre,
        size_band=size_band,
        include_superstars=include_superstars,
        limit=limit,
    )


@mcp.tool(annotations=READS_LIVE)
def artist_profile(name: str) -> dict[str, Any]:
    """Full picture of one artist: breakout signals, daily listener trend over the last 30
    days with a pattern label (steady vs. concentrated in one day), the latest scout brief
    (with signing status) if one exists, and upcoming shows. Use for "tell me about X" or
    "is X's growth real?".

    The name is fuzzy-matched. If the result has matched=false, do NOT pick one yourself:
    show the user the alternatives and ask which they meant. Shows carry role/headliner and
    live ticket_status, as in upcoming_shows.
    """
    return _with_tickets(_call(ar_data.artist_profile, name=name), key="upcoming_shows")


@mcp.tool(annotations=READS_LIVE)
def upcoming_shows(
    city: str | None = None,
    max_price: float | None = None,
    days_ahead: int = 30,
    min_breakout_score: float | None = None,
    genre: str | None = None,
    require_price: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Upcoming concerts (Toronto and Montreal only) by tracked artists, highest breakout
    score first. Use for "cheap shows near me", "who should I go see", "rising artists playing
    this month". If the user says "near me" without a city, ask which city.

    Every show has role ("headliner" or "support"), headliner and lineup: always say when the
    artist is only opening ("opening for Bonobo"), since it's mainly the headliner's show.
    ticket_status is checked live: "on sale", "not on sale yet", "off sale" (not selling
    during its sale window: often sold out, but NOT confirmed, so don't say "sold out"),
    "sales closed", "rescheduled", "postponed" or "unknown". Canceled shows are removed.

    Args:
        city: "Toronto" or "Montreal" (accents optional).
        max_price: highest acceptable minimum ticket price, in CAD. Shows with no listed price
            are kept and marked price_known=false; mention that rather than calling them free.
        days_ahead: window length, 1-90 (default 30). It starts today (or at the snapshot
            date if that is later), so shows that already happened are never returned.
        min_breakout_score: e.g. 70 for "rising" artists only.
        genre: a Last.fm tag such as "indie pop".
        require_price: only shows with a listed price. Most shows list none, so for "under
            $X" questions, if priced_matches > 0, call again with this set so confirmed
            cheap shows aren't crowded out.
        limit: rows to return, max 50. total_matches says how many matched in all.
    """
    return _with_tickets(
        _call(
            ar_data.upcoming_shows,
            city=city,
            max_price=max_price,
            days_ahead=days_ahead,
            min_breakout_score=min_breakout_score,
            genre=genre,
            require_price=require_price,
            limit=limit,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def data_status() -> dict[str, Any]:
    """When the data was captured and exported, how many days old it is, and row counts per
    table. Use when the user asks how fresh the data is, and before claims about "this week"."""
    return _call(ar_data.snapshot_info)


def _client() -> LastFmClient:
    global _lastfm
    if _lastfm is None:
        try:  # .env locally (also loads LASTFM_USERNAME), secret scope on Databricks
            key = get_secret("LASTFM_API_KEY", "lastfm_key")
        except RuntimeError as e:
            raise ar_data.QueryError(
                "Last.fm API key not configured", "Set LASTFM_API_KEY in .env."
            ) from e  # noqa: E501
        _lastfm = LastFmClient(key, requests_per_sec=config.LASTFM_REQUESTS_PER_SEC)
    return _lastfm


def _personal(fn, username: str | None, **kwargs) -> dict[str, Any]:
    def run():
        client = _client()
        user = (username or os.environ.get("LASTFM_USERNAME") or "").strip()
        if not user:
            raise ar_data.QueryError(
                "No Last.fm username", "Ask the user for their Last.fm username."
            )  # noqa: E501
        return fn(client, user, **kwargs)

    run.__name__ = fn.__name__
    return _call(run)


@mcp.tool(annotations=READS_LIVE)
def my_taste_profile(username: str | None = None) -> dict[str, Any]:
    """The user's taste from their Last.fm listening: top artists and tags, how much of it we
    track (coverage), how much is emerging (<500k listeners), and which artists in THEIR
    library (all-time top 500) are breaking now (breakout >= 70, under 2M listeners). Use for
    "what's my taste?", "are any of my artists blowing up?". username: their Last.fm username;
    omit to use the configured one.
    """
    return _personal(personal.taste_profile, username)


@mcp.tool(annotations=READS_LIVE)
def discover_for_me(
    username: str | None = None,
    genre: str | None = None,
    size_band: str | None = None,
    min_breakout_score: float = 70,
    limit: int = 10,
) -> dict[str, Any]:
    """Rising artists new to the user, ranked by taste match x breakout score x novelty.
    Artists they've barely played (a song in passing) can still appear, marked with
    your_plays; favourites never do. Each has `because` (plays, the user's artists it's
    similar to, shared tags): quote it.
    listeners_7d_growth_pct is in percent (0.21 = +0.21%); breakout_score is what's unusual.
    Use for "who should I check out?", "recommend new artists for me".

    Args:
        username: their Last.fm username; omit to use the configured one.
        genre, size_band: optional filters, as in top_breakout_artists.
        min_breakout_score: default 70 ("rising").
        limit: max 50.
    """
    return _personal(personal.discover, username, genre=genre, size_band=size_band,
                     min_breakout_score=min_breakout_score, limit=limit)  # fmt: skip


@mcp.tool(annotations=READS_LIVE)
def shows_for_me(
    username: str | None = None,
    city: str | None = None,
    max_price: float | None = None,
    days_ahead: int = 30,
    who: Literal["any", "mine", "new"] = "any",
    prefer_cheap: bool = False,
    limit: int = 10,
) -> dict[str, Any]:
    """Upcoming Toronto/Montreal shows picked for the user: artists they play (they may not
    know about the show) and artists that fit their taste, scored together. Each show has
    `why`, `rank_score` and `score_parts`. Same city, price (CAD; price_known=false means
    unlisted), date, role/headliner and live ticket_status rules as upcoming_shows; opening
    slots rank lower (billing 0.8) and their `why` starts "opening for <headliner>".

    Set the knobs from the user's wording. Nothing is excluded; they only shift the ranking:
        who: "mine" leans on artists they play a lot ("a show by an artist I listen to");
            "new" leans on artists new to them ("someone new", "haven't heard"), where a song
            played in passing still counts as new; "any" = balanced.
        prefer_cheap: True when they stress price ("cheap", "on a budget"). Ranks listed low
            prices higher; use max_price too if they give a number.
    Examples: "cheap show by an artist I listen to" -> who="mine", prefer_cheap=True.
    "a new artist that's cheap" -> who="new", prefer_cheap=True. "any shows for me" -> defaults.
    """
    return _with_tickets(
        _personal(personal.shows_for_me, username, city=city, max_price=max_price,
                  days_ahead=days_ahead, who=who, prefer_cheap=prefer_cheap, limit=limit)
    )  # fmt: skip


@mcp.resource("ar-radar://definitions", mime_type="text/markdown")
def definitions() -> str:
    """Plain-English definitions: breakout score, size bands, growth windows, country and
    genre matching, trend patterns, signing status."""
    return (CONTEXT / "definitions.md").read_text()


@mcp.resource("ar-radar://limitations", mime_type="text/markdown")
def limitations() -> str:
    """What the data can't tell you: Last.fm skew, Ticketmaster gaps, short history."""
    return (CONTEXT / "limitations.md").read_text()


@mcp.prompt()
def scout_report(genre: str, country: str = "") -> str:
    """A short scouting brief on the top rising artists in a genre (and optionally a country)."""
    where = f" in {country}" if country else ""
    return (
        f"Write a short A&R scouting report on rising {genre} artists{where}.\n"
        f"1. Call data_status and state the data date.\n"
        f"2. Call top_breakout_artists(genre={genre!r}"
        + (f", country={country!r}" if country else "")
        + ", limit=10).\n"
        "3. Call artist_profile on the top 3 and say whether each one's growth is steady or "
        "concentrated in one day, and what their signing status says.\n"
        "4. Cite the numbers (score, size band, 7d/14d growth). Read ar-radar://limitations "
        "and end with two caveats that matter for this genre. Never predict success."
    )


@mcp.prompt()
def my_report(username: str = "") -> str:
    """A personal scouting report from the user's Last.fm listening."""
    user = f"username={username!r}" if username else "the configured username"
    return (
        f"Write a short personal A&R report for {user}.\n"
        "1. my_taste_profile: summarize their taste in 2 sentences and list their artists "
        "that are breaking now.\n"
        "2. discover_for_me: 5 rising artists they don't know yet, each with its `because`.\n"
        "3. shows_for_me: up to 3 upcoming shows worth going to, with why and price.\n"
        "Give the data date (data_status) and never predict success."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="A&R Radar MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    log.info("snapshot: %s", ar_data.db_path())
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
