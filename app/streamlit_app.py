"""A&R Radar in the browser. Type a Last.fm username, then pick: shows for you, new artists,
your taste, who's rising, or ask a question. Runs locally at $0:

    streamlit run app/streamlit_app.py

Everything except "Ask" calls the MCP server's own Python functions directly (no AI): the
same ranking, matching and live ticket checks. "Ask" runs Claude Code on this machine.
Toronto and Montréal come from the daily snapshot; any other city is searched live on
Ticketmaster (live_events.py).
"""

import datetime as dt
import html
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp_server"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

import ar_data  # noqa: E402
import chat  # noqa: E402
import live_events  # noqa: E402
import personal  # noqa: E402
import tickets  # noqa: E402

load_dotenv(REPO / ".env")  # LASTFM_API_KEY, TICKETMASTER_API_KEY, optional LASTFM_USERNAME
st.set_page_config(page_title="A&R Radar", layout="wide")

WHO = {"Balanced": "any", "Artists I play": "mine", "New to me": "new"}
# Countries Ticketmaster sells in; only needed when a city name is ambiguous (London).
COUNTRIES = {
    "Any country": None, "Canada": "CA", "United States": "US", "United Kingdom": "GB",
    "Ireland": "IE", "Australia": "AU", "New Zealand": "NZ", "Mexico": "MX", "Germany": "DE",
    "France": "FR", "Spain": "ES", "Netherlands": "NL", "Belgium": "BE", "Sweden": "SE",
    "Norway": "NO", "Denmark": "DK", "Austria": "AT", "Switzerland": "CH", "Poland": "PL",
}  # fmt: skip
NEEDS_ATTENTION = {"off sale", "rescheduled", "postponed", "sales closed"}

CSS = """
<style>
h1, h2, h3 { letter-spacing: -0.01em; }
/* Streamlit gives each markdown block a -1rem bottom margin; ours need their full height. */
[data-testid="stMarkdownContainer"]:has(> .td, > .th, > .meta, > .lead, > .elsewhere) {
  margin-bottom: 0 !important; }
.stTabs button[role="tab"] p { font-family: "Archivo Narrow", sans-serif; font-weight: 700;
  font-size: 1.05rem; text-transform: uppercase; letter-spacing: 0.04em; }
[class*="st-key-list-"], [class*="st-key-tbl-"] { gap: 0 !important; }
[class*="st-key-item-"] { border-top: 1px solid #D3CAB8; padding: 0.85rem 0 0.8rem; }
[class*="st-key-item-"] [data-testid="stVerticalBlock"] { gap: 0.1rem; }
[class*="st-key-list-"] > div:last-child[class*="st-key-item-"] {
  border-bottom: 1px solid #D3CAB8; }
.lead { font-family: "Archivo Narrow", sans-serif; text-align: center; line-height: 1.05;
  text-transform: uppercase; }
.lead .top { font-size: 0.78rem; letter-spacing: 0.08em; color: #C8401E; font-weight: 700; }
.lead .big { font-size: 2rem; font-weight: 700; }
.lead .low { font-size: 0.75rem; color: #6E685D; letter-spacing: 0.06em; }
[class*="st-key-open-"] button { padding: 0; min-height: 0; justify-content: flex-start; }
[class*="st-key-open-"] button p { font-family: "Archivo Narrow", sans-serif; font-weight: 700;
  font-size: 1.3rem; text-transform: uppercase; letter-spacing: 0.01em; color: #1C1B18;
  text-align: left; line-height: 1.15; }
[class*="st-key-open-"] button:hover p { color: #C8401E; text-decoration: underline;
  text-underline-offset: 3px; }
[class*="st-key-row-"] [class*="st-key-open-"] button p { font-family: inherit;
  font-size: 0.92rem; text-transform: none; font-weight: 500; letter-spacing: 0; }
.meta { color: #4A463F; font-size: 0.92rem; }
.facts { margin-top: 0.3rem; font-family: "IBM Plex Mono", monospace; font-size: 0.78rem;
  color: #4A463F; text-transform: uppercase; letter-spacing: 0.02em; }
.facts .sep { margin: 0 0.5rem; color: #B4AB98; font-style: normal; }
.facts .warn { color: #A3170F; background: #F7D9D3; padding: 0.05rem 0.35rem; font-weight: 600; }
.why { margin-top: 0.3rem; font-size: 0.88rem; color: #6E685D; }
.cta { display: inline-block; font-family: "Archivo Narrow", sans-serif;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.9rem;
  color: #1C1B18 !important; border: 1.5px solid #1C1B18; padding: 0.35rem 0.8rem;
  text-decoration: none !important; }
.cta:hover { background: #1C1B18; color: #F4F0E8 !important; }
.pricenote { font-size: 0.82rem; color: #4A463F; border-left: 3px solid #A3170F;
  padding: 0.3rem 0 0.3rem 0.7rem; margin: 0.2rem 0 0.8rem; }
.chips span { display: inline-block; border: 1px solid #B4AB98; padding: 0.1rem 0.5rem;
  margin: 0 0.35rem 0.4rem 0; font-size: 0.85rem; }
[class*="st-key-head-"] { border-bottom: 1.5px solid #1C1B18; padding-bottom: 0.3rem; }
.th { font-family: "IBM Plex Mono", monospace; font-size: 0.72rem; text-transform: uppercase;
  color: #6E685D; }
[class*="st-key-row-"] { border-bottom: 1px solid #D3CAB8; padding: 0.4rem 0; }
.td { font-size: 0.92rem; line-height: 1.6rem; }
[class*="st-key-row-"] button { height: 1.6rem; }
[class*="st-key-row-"] [data-testid="stElementContainer"] { margin: 0; }
.td.n { font-family: "IBM Plex Mono", monospace; font-size: 0.85rem; }
.td.untracked { color: #6E685D; cursor: default; }
.noprofile { font-family: "IBM Plex Mono", monospace; font-size: 0.68rem; text-transform: uppercase;
  border: 1px dashed #B4AB98; padding: 0 0.3rem; margin-left: 0.5rem; vertical-align: 1px; }
.elsewhere { font-size: 0.9rem; margin: 0.15rem 0; }
.note { font-size: 0.82rem; color: #6E685D; margin-top: 0.6rem; }
</style>
"""

e = html.escape  # every value from the data goes through this before it reaches the page


def safe(fn, *args, **kwargs):
    """Show bad input / missing setup as a plain message instead of a stack trace."""
    try:
        return fn(*args, **kwargs)
    except ar_data.QueryError as err:
        st.warning(f"{err}. {err.hint}")
        return None


# Cached for 10 minutes: Streamlit reruns the whole script on every click.
@st.cache_data(ttl=600, show_spinner="Reading your Last.fm")
def taste(user):
    return personal.taste_profile(personal.client(), user)


@st.cache_data(ttl=600, show_spinner="Finding artists you'd like")
def discover(user, genre, band, limit):
    return personal.discover(personal.client(), user, genre=genre, size_band=band, limit=limit)


@st.cache_data(ttl=600, show_spinner="Searching shows and ranking them for you")
def my_shows(user, city, country, who, cheap, max_price, days):
    return personal.shows_for_me(
        personal.client(), user, city=city, who=who, prefer_cheap=cheap, max_price=max_price,
        days_ahead=days, limit=15, live=live_events.for_city(city, country, days),
    )  # fmt: skip


@st.cache_data(ttl=600, show_spinner="Searching shows")
def rising_shows(city, country):
    return ar_data.upcoming_shows(
        city=city, min_breakout_score=70, limit=10, live=live_events.for_city(city, country, 30)
    )


def with_ticket_status(result, key="shows"):
    result = dict(result, **{key: [dict(s) for s in result[key]]})  # don't mutate the cache
    return tickets.annotate(result, tickets.api_key(), key=key)  # live, cached 10 min


def pct(value):
    return "n/a" if value is None else f"{value:+.2f}%"


def listeners(n):
    if not n:
        return "listeners unknown"
    return f"{n / 1e6:.1f}M listeners" if n >= 1e6 else f"{n / 1e3:.0f}k listeners"


def clock(local_time):
    try:
        return dt.time.fromisoformat(local_time).strftime("%-I:%M %p").lower()
    except (TypeError, ValueError):
        return None


PRICE_NOTE = (
    "Prices come from Ticketmaster's public API, which often leaves them out, especially for "
    "big shows. It doesn't say why; dynamic or tiered pricing is a common cause. Listed prices "
    "can also be out of date or exclude fees. The ticket page always has the real price."
)


def price_note():
    st.markdown(f'<div class="pricenote">{e(PRICE_NOTE)}</div>', unsafe_allow_html=True)


def open_button(uid, name, artist_key):
    """The artist's name; clicking it opens their profile. Untracked artists are plain text."""
    if not artist_key:  # say why it isn't clickable rather than just leaving it plain
        st.markdown(
            f'<div class="td untracked">{e(name)}<span class="noprofile">no profile</span></div>',
            unsafe_allow_html=True,
        )
    elif st.button(name, key=f"open-{uid}", type="tertiary"):
        open_profile(artist_key, name)


def item(uid, lead, title, artist_key, meta, facts, why, cta, url, clickable=True):
    """One listing row: a lead column (date or rank), the details, and a link out."""
    sep = '<i class="sep">/</i>'
    facts_html = sep.join(f'<span class="{cls}">{e(text)}</span>' for text, cls in facts)
    with st.container(key=f"item-{uid}"):
        left, body, right = st.columns([0.8, 7, 1.3], gap="medium", vertical_alignment="center")
        left.markdown(f'<div class="lead">{lead}</div>', unsafe_allow_html=True)
        with body:
            if clickable:
                open_button(uid, title, artist_key)
            else:
                st.markdown(f"**{e(title)}**")
            st.markdown(
                f'<div class="meta">{e(meta)}</div><div class="facts">{facts_html}</div>'
                + (f'<div class="why">{e(why)}</div>' if why else ""),
                unsafe_allow_html=True,
            )
        if url and url.startswith("https://"):
            right.markdown(
                f'<a class="cta" href="{e(url, quote=True)}" target="_blank" rel="noopener">'
                f"{cta}</a>",
                unsafe_allow_html=True,
            )


def show_item(uid, show, clickable=True):
    day = dt.date.fromisoformat(show["event_date"])
    lead = (
        f'<div class="top">{day:%b}</div><div class="big">{day.day}</div>'
        f'<div class="low">{day:%a}</div>'
    )
    venue, city = show["venue"] or "Venue not listed", show["city"] or ""
    where = venue if city in venue else f"{venue}, {city}"
    meta = " · ".join(p for p in (where, clock(show["local_time"])) if p)
    status = show.get("ticket_status", "unknown")
    price = (
        (f"{show['min_price']:.0f} {show['currency'] or ''}".strip(), "")
        if show["price_known"]
        else ("Price not listed", "warn")
    )
    facts = [
        (f"Opening for {show['headliner']}" if show["role"] == "support" else "Headliner", ""),
        (status, "warn" if status in NEEDS_ATTENTION else ""),
        price,
    ]
    if show.get("breakout_score") is not None:
        facts.append((f"Breakout {show['breakout_score']:.0f}", ""))
    why = show.get("why") or ""
    if show["role"] == "support":  # already in the facts line
        why = why.removeprefix(f"opening for {show['headliner']}").removeprefix("; ")
    if show.get("ticket_note") and status != "on sale":
        why = f"{why}. {show['ticket_note']}" if why else show["ticket_note"]
    uid = f"{uid}-{show['event_id']}-{show['artist_key']}"
    item(uid, lead, show["artist_name"], show["artist_key"], meta, facts, why, "Tickets",
         show["ticket_url"], clickable)  # fmt: skip


def lastfm_url(name):
    return f"https://www.last.fm/music/{quote_plus(name)}"


def artist_item(uid, rank, artist):
    facts = [
        (f"Breakout {artist['breakout_score']:.0f}", ""),
        (f"Listeners {pct(artist['listeners_7d_growth_pct'])} / 7 days", ""),
    ]
    if artist.get("your_plays"):
        facts.append((f"You've played them {artist['your_plays']}x", ""))
    meta = f"{listeners(artist['lastfm_listeners'])} · {artist['genres'] or 'no tags'}"
    lead = f'<div class="top">No.</div><div class="big">{rank:02d}</div>'
    item(f"{uid}-{rank}", lead, artist["name"], artist["artist_key"], meta, facts,
         artist["because"], "Listen", lastfm_url(artist["name"]))  # fmt: skip


def table(uid, rows, columns, widths):
    """A ranked list whose artist names open profiles. columns: (header, key, kind), kind is
    "n" (number), "t" (text) or "artist" (needs name + artist_key in the row)."""
    with st.container(key=f"tbl-{uid}"):
        with st.container(key=f"head-{uid}"):
            for col, (header, _, _) in zip(st.columns(widths), columns, strict=True):
                col.markdown(f'<div class="th">{e(header)}</div>', unsafe_allow_html=True)
        for i, r in enumerate(rows):
            with st.container(key=f"row-{uid}-{i}"):
                cells = st.columns(widths, vertical_alignment="center")
                for col, (_, k, kind) in zip(cells, columns, strict=True):
                    with col:
                        if kind == "artist":
                            open_button(f"{uid}-{i}", r["name"], r.get("artist_key"))
                        else:
                            css = "td n" if kind == "n" else "td"
                            st.markdown(f'<div class="{css}">{e(str(r[k]))}</div>',
                                        unsafe_allow_html=True)  # fmt: skip


# --- artist profile (opens over the page) ------------------------------------------------------


@st.cache_data(ttl=600, show_spinner=False)
def profile_data(artist_key):
    return ar_data.artist_profile(artist_key=artist_key)


@st.cache_data(ttl=600, show_spinner=False)
def artist_shows(artist_key, city, country):
    live = live_events.for_city(city, country, 90)
    return ar_data.upcoming_shows(
        city=city, days_ahead=90, artist_keys=[artist_key], limit=20, live=live
    )


INK, MUTED, ACCENT, GRID = "#1C1B18", "#6E685D", "#C8401E", "#E2DBCD"
BREAK_DAYS = 3  # a gap longer than this is drawn dashed: no data, not a trend


def history_chart(points):
    """Daily Last.fm listeners: a solid line where we have days, dashed across a pause."""
    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    gap = df["date"].diff().dt.days > BREAK_DAYS
    df["run"] = gap.cumsum()
    # Each pause becomes a pair of points: the last day before it and the first day after.
    ends = df[gap.shift(-1, fill_value=False)].reset_index(drop=True)
    starts = df[gap].reset_index(drop=True)
    joins = pd.concat([ends.assign(pair=ends.index), starts.assign(pair=starts.index)])
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%b %d", grid=False, tickCount=6,
              labelColor=MUTED, domainColor=GRID, tickColor=GRID))  # fmt: skip
    y = alt.Y("listeners:Q", title=None, scale=alt.Scale(zero=False),
              axis=alt.Axis(format="~s", gridColor=GRID, domain=False, ticks=False,
                            labelColor=MUTED, tickCount=4))  # fmt: skip
    hover = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    base = alt.Chart(df).encode(x=x, y=y)
    layers = [
        alt.Chart(joins).mark_line(color=MUTED, strokeDash=[4, 4], strokeWidth=1.5)
        .encode(x=x, y=y, detail="pair:N"),
        base.mark_line(color=ACCENT, strokeWidth=2).encode(detail="run:N"),
        base.mark_rule(color=MUTED)
        .encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0))),
        base.mark_point(filled=True, size=64, color=ACCENT, stroke="#F4F0E8", strokeWidth=2)
        .encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("date:T", title="Date", format="%b %d, %Y"),
                     alt.Tooltip("listeners:Q", title="Listeners", format=",")],
        )
        .add_params(hover),
    ]  # fmt: skip
    return (
        alt.layer(*layers)
        .properties(height=220)
        .configure(background="transparent", font="IBM Plex Sans")
        .configure_view(stroke=None)
        .configure_axis(labelFont="IBM Plex Mono", labelFontSize=11)
    )


def open_profile(artist_key, name):
    st.dialog(name, width="large")(profile_body)(artist_key, name)


def profile_body(artist_key, name):
    p = safe(profile_data, artist_key)
    if not p:
        return
    s = p["signals"]
    st.markdown(
        f'<div class="meta">{e(listeners(s["lastfm_listeners"]))} · {e(s["size_band"] or "")}'
        f" · {e(s['genres'] or 'no tags')}</div>",
        unsafe_allow_html=True,
    )
    m1, m2, m3 = st.columns(3)
    score = s["breakout_score"]
    m1.metric("Breakout score", "n/a" if score is None else f"{score:.0f}")
    m2.metric("Listeners, 7 days", pct(s["listeners_7d_growth_pct"]))
    m3.metric("Listeners, 14 days", pct(s["listeners_14d_growth_pct"]))

    st.subheader("Last.fm listeners")
    if len(p["daily_listeners"]) >= 2:
        st.altair_chart(history_chart(p["daily_listeners"]), theme=None, width="stretch")
        for g in p["trend"].get("gaps", []):
            if g["days"] > BREAK_DAYS:
                st.caption(
                    f"No data from {g['from']} to {g['to']}: daily collection was paused, so "
                    "the dashed line only joins the two ends."
                )
    else:
        st.caption("Not enough daily history yet for a chart.")

    st.subheader(f"Shows in {city}")
    with st.spinner(f"Searching Ticketmaster for shows in {city}"):
        shows = safe(artist_shows, artist_key, city, country)
    if shows is not None:
        shows = with_ticket_status(shows)
        if shows["shows"]:
            price_note()
            with st.container(key=f"list-dialog-{artist_key}"):
                for show in shows["shows"]:
                    show_item("dialog", show, clickable=False)
        else:
            st.markdown(
                f"No upcoming {e(name)} shows in {e(city)} on Ticketmaster in the next 90 days."
            )
            st.caption(
                "Source: Ticketmaster Discovery API. Small venues often sell through Dice, "
                "Eventbrite or their own sites, so check the artist's own page too."
            )
    tracked = ar_data._fold(city) in {"toronto", "montreal"}
    elsewhere = [
        x
        for x in p["upcoming_shows"]
        if not tracked or ar_data._fold(x["city"]) != ar_data._fold(city)
    ]  # noqa: E501
    if elsewhere:
        st.caption("Also on Ticketmaster in Toronto and Montréal:")
        for x in elsewhere[:5]:
            st.markdown(
                f'<div class="elsewhere">{e(x["event_date"])} · {e(x["venue"] or "")}, '
                f"{e(x['city'] or '')}</div>",
                unsafe_allow_html=True,
            )

    brief = p.get("scout_brief")
    if brief:
        with st.expander(f"Scout brief ({brief['as_of_date']})"):
            st.markdown(brief["brief_md"])
    links = [("Last.fm", s.get("lastfm_url") or lastfm_url(name)),
             ("Tour dates on Last.fm", f"{lastfm_url(name)}/+events")]  # fmt: skip
    st.markdown(
        " ".join(
            f'<a class="cta" href="{e(u, quote=True)}" target="_blank" rel="noopener">{t}</a>'
            for t, u in links
        ),
        unsafe_allow_html=True,
    )


# Tool-facing wording in the shared notes that means nothing on a web page.
NOTE_EDITS = {
    " price_known=false means no listed price, not free.": " A missing price isn't free.",
    "; price_known=false means no listed price, not free.": ". A missing price isn't free.",
    "; set include_superstars to include": "",
}


def notes(result):
    for note in result.get("notes", []):
        for old, new in NOTE_EDITS.items():
            note = note.replace(old, new)
        st.markdown(f'<div class="note">{e(note)}</div>', unsafe_allow_html=True)


st.html(CSS)

# --- sidebar ---------------------------------------------------------------------------------

with st.sidebar:
    st.title("A&R Radar")
    st.caption("Artists growing fast for their size, and where to see them play.")
    user = st.text_input(
        "Last.fm username", value=os.environ.get("LASTFM_USERNAME", ""), key="username"
    ).strip()
    city = st.text_input("City", value="Toronto", key="city").strip() or "Toronto"
    country = COUNTRIES[st.selectbox("Country", list(COUNTRIES), key="country")]
    status = safe(ar_data.snapshot_info)
    if status:
        age = status["days_since_data"]
        st.caption(
            f"Rankings as of {status['data_as_of']} ({age} day{'s' if age != 1 else ''} old)"
        )  # noqa: E501
    st.caption(
        "Toronto and Montréal are tracked daily. Other cities are searched live on "
        "Ticketmaster and list rising artists touring through. Listening data is read live "
        "from Last.fm and never stored."
    )

tab_shows, tab_discover, tab_taste, tab_rising, tab_ask = st.tabs(
    ["Shows", "Discover", "Your taste", "Rising", "Ask"]
)
need_user = "Enter your Last.fm username in the sidebar to personalize this."

with tab_shows:
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1], vertical_alignment="bottom")
    who = (
        c1.segmented_control("Whose shows", list(WHO), default="Balanced", key="who") or "Balanced"
    )  # noqa: E501
    cheap = c2.toggle("Prefer cheap", key="cheap")
    max_price = c3.number_input("Max price", min_value=0, value=0, step=5, key="max_price")
    days = c4.slider("Days ahead", 7, 90, 30, key="days")
    if not user:
        st.info(need_user)
    else:
        result = safe(my_shows, user, city, country, WHO[who], cheap, max_price or None, days)
        if result:
            result = with_ticket_status(result)
            st.caption(
                f"{result['total_matches']} shows in {city} linked to your listening. "
                f"Best {result['count']}:"
            )
            price_note()
            with st.container(key="list-shows"):
                for show in result["shows"]:
                    show_item("shows", show)
            if not result["shows"]:
                st.info(
                    "Nothing in this window. Try more days ahead, another city, or no price cap."
                )  # noqa: E501
            notes(result)

with tab_discover:
    c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
    genre = c1.text_input("Genre", placeholder="e.g. indie pop, hip-hop", key="genre")
    band = c2.selectbox("Size", ["Any (under 2M)", *ar_data.SIZE_BANDS[:-1]], key="band")
    limit = c3.slider("How many", 5, 30, 10, key="limit")
    if not user:
        st.info(need_user)
    else:
        args = (user, genre.strip() or None, None if band.startswith("Any") else band, limit)
        result = safe(discover, *args)
        if result:
            with st.container(key="list-discover"):
                for rank, artist in enumerate(result["artists"], 1):
                    artist_item("discover", rank, artist)
            if not result["artists"]:
                st.info("No rising matches. Try a broader genre or another size.")
            notes(result)

with tab_taste:
    if not user:
        st.info(need_user)
    else:
        profile = safe(taste, user)
        if profile:
            m1, m2, m3 = st.columns(3)
            m1.metric("Scrobbles", f"{profile['total_scrobbles']:,}")
            m2.metric("Top artists we track", f"{profile['coverage']:.0%}")
            m3.metric("Listening to emerging acts", f"{profile['emerging_share']:.0%}")
            st.subheader("Your top tags")
            chips = "".join(f"<span>{e(t)}</span>" for t in profile["top_tags"])
            st.markdown(f'<div class="chips">{chips}</div>', unsafe_allow_html=True)
            left, right = st.columns(2, gap="large")
            with left:
                st.subheader("Your top artists")
                rows = [
                    {"n": i, "name": a["name"], "artist_key": a["artist_key"],
                     "plays": a["your_playcount"], "size": a["size_band"] or "not tracked"}
                    for i, a in enumerate(profile["top_artists"], 1)
                ]  # fmt: skip
                table("top", rows, [("#", "n", "n"), ("Artist", "name", "artist"),
                                    ("Plays", "plays", "n"), ("Listeners", "size", "t")],
                      [0.5, 4, 1.4, 1.8])  # fmt: skip
            with right:
                st.subheader("Your artists breaking now")
                st.caption("Rising, under 1M listeners. Smaller and more-played rank higher.")
                rows = [
                    {"n": i, "name": a["name"], "artist_key": a["artist_key"],
                     "score": f"{a['breakout_score']:.0f}",
                     "size": listeners(a["lastfm_listeners"]).removesuffix(" listeners"),
                     "plays": a["your_playcount"]}
                    for i, a in enumerate(profile["your_artists_breaking"], 1)
                ]  # fmt: skip
                if rows:
                    table("breaking", rows, [("#", "n", "n"), ("Artist", "name", "artist"),
                                             ("Breakout", "score", "n"), ("Listeners", "size", "n"),
                                             ("Plays", "plays", "n")],
                          [0.5, 3.5, 1.4, 2.2, 1.4])  # fmt: skip
                else:
                    st.info("None of your smaller artists are rising right now.")
            notes(profile)

with tab_rising:
    c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
    chart = c1.selectbox("Country charts", ["Any", *ar_data.COUNTRIES], key="chart_country")
    r_genre = c2.text_input("Genre", placeholder="e.g. shoegaze", key="r_genre")
    r_band = c3.selectbox("Size", ["Any (under 2M)", *ar_data.SIZE_BANDS[:-1]], key="r_band")
    ranked = safe(
        ar_data.top_breakout_artists,
        country=None if chart == "Any" else chart,
        genre=r_genre.strip() or None,
        size_band=None if r_band.startswith("Any") else r_band,
        limit=20,
    )
    if ranked:
        rows = [
            {"n": i, "name": a["name"], "artist_key": a["artist_key"],
             "score": f"{a['breakout_score']:.0f}", "size": a["size_band"],
             "growth": pct(a["listeners_7d_growth_pct"]), "genres": a["genres"] or ""}
            for i, a in enumerate(ranked["artists"], 1)
        ]  # fmt: skip
        st.caption("Click an artist for their listening history and shows.")
        table("rising", rows, [("#", "n", "n"), ("Artist", "name", "artist"),
                               ("Breakout", "score", "n"), ("Size", "size", "t"),
                               ("7-day growth", "growth", "n"), ("Genres", "genres", "t")],
              [0.5, 3, 1.2, 1.3, 1.6, 4])  # fmt: skip
        notes(ranked)
    st.subheader(f"Rising artists playing {city}")
    shows = safe(rising_shows, city, country)
    if shows:
        if shows["shows"]:
            price_note()
        with st.container(key="list-rising"):
            for show in with_ticket_status(shows)["shows"]:
                show_item("rising", show)
        if not shows["shows"]:
            st.info(f"No rising artists (breakout 70+) playing {city} in the next 30 days.")

with tab_ask:
    st.caption(
        "Ask anything about rising artists and shows. Answers come from Claude Code on this "
        "machine, using only the A&R Radar tools. Expect 10 to 20 seconds."
    )
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("chat_session", None)
    if not chat.available():
        st.warning("This needs the `claude` command-line tool installed and logged in.")
    else:
        history = st.container(height=480)
        for msg in st.session_state.messages:
            history.chat_message(msg["role"]).markdown(msg["content"])
        question = st.chat_input("e.g. Any cheap shows this month by artists like Frank Ocean?")
        if st.button("New conversation", key="reset_chat"):
            st.session_state.messages, st.session_state.chat_session = [], None
            st.rerun()
        if question:
            history.chat_message("user").markdown(question)
            prompt = question
            if st.session_state.chat_session is None:
                context = f"My Last.fm username is {user}. " if user else ""
                where = f"{city} ({country})" if country else city
                prompt = f"({context}I'm in {where}.) {question}"
            with history.chat_message("assistant"):
                with st.status("Thinking", expanded=False) as progress:
                    reply = chat.ask(
                        prompt,
                        st.session_state.chat_session,
                        on_tool=lambda name: progress.update(label=chat.label(name)),
                    )
                    progress.update(label="Done", state="error" if reply["error"] else "complete")
                answer = reply["text"] if not reply["error"] else f"Error: {reply['error']}"
                st.markdown(answer)
            st.session_state.chat_session = reply["session_id"]
            st.session_state.messages += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
