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
.stTabs button[role="tab"] p { font-family: "Archivo Narrow", sans-serif; font-weight: 700;
  font-size: 1.05rem; text-transform: uppercase; letter-spacing: 0.04em; }
.item { display: grid; grid-template-columns: 4.2rem 1fr auto; gap: 0 1.1rem;
  padding: 0.95rem 0; border-top: 1px solid #D3CAB8; align-items: start; }
.item:last-child { border-bottom: 1px solid #D3CAB8; }
.lead { font-family: "Archivo Narrow", sans-serif; text-align: center; line-height: 1.05;
  text-transform: uppercase; }
.lead .top { font-size: 0.78rem; letter-spacing: 0.08em; color: #C8401E; font-weight: 700; }
.lead .big { font-size: 2rem; font-weight: 700; }
.lead .low { font-size: 0.75rem; color: #6E685D; letter-spacing: 0.06em; }
.title { font-family: "Archivo Narrow", sans-serif; font-weight: 700; font-size: 1.3rem;
  text-transform: uppercase; letter-spacing: 0.01em; line-height: 1.15; }
.meta { color: #4A463F; font-size: 0.92rem; margin-top: 0.1rem; }
.facts { margin-top: 0.4rem; font-family: "IBM Plex Mono", monospace; font-size: 0.78rem;
  color: #4A463F; text-transform: uppercase; letter-spacing: 0.02em; }
.facts span + span::before { content: "/"; margin: 0 0.5rem; color: #B4AB98; }
.facts .hot { color: #C8401E; font-weight: 500; }
.why { margin-top: 0.35rem; font-size: 0.88rem; color: #6E685D; }
.cta { align-self: center; font-family: "Archivo Narrow", sans-serif; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.9rem; color: #1C1B18 !important;
  border: 1.5px solid #1C1B18; padding: 0.35rem 0.8rem; text-decoration: none !important; }
.cta:hover { background: #1C1B18; color: #F4F0E8 !important; }
.chips span { display: inline-block; border: 1px solid #B4AB98; padding: 0.1rem 0.5rem;
  margin: 0 0.35rem 0.4rem 0; font-size: 0.85rem; }
table.list { width: 100%; border-collapse: collapse; font-size: 0.92rem; border: none; }
table.list th, table.list td { border: none !important; background: none !important; }
table.list th { text-align: left; font-family: "IBM Plex Mono", monospace; font-size: 0.72rem;
  text-transform: uppercase; color: #6E685D; font-weight: 500; padding: 0.3rem 0.6rem 0.3rem 0;
  border-bottom: 1.5px solid #1C1B18 !important; }
table.list td { padding: 0.45rem 0.6rem 0.45rem 0; border-bottom: 1px solid #D3CAB8 !important; }
table.list td.n { font-family: "IBM Plex Mono", monospace; font-size: 0.85rem; }
.note { font-size: 0.82rem; color: #6E685D; margin-top: 0.6rem; }
@media (max-width: 640px) {
  .item { grid-template-columns: 3.2rem 1fr; }
  .cta { grid-column: 2; justify-self: start; margin-top: 0.5rem; }
}
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


def item(lead, title, meta, facts, why, cta, url):
    """One listing row: a lead column (date or rank), the details, and a link out."""
    facts_html = "".join(f'<span class="{cls}">{e(text)}</span>' for text, cls in facts)
    link = (
        f'<a class="cta" href="{e(url, quote=True)}" target="_blank" rel="noopener">{cta}</a>'
        if url and url.startswith("https://")
        else "<span></span>"
    )
    st.markdown(
        f'<div class="item"><div class="lead">{lead}</div><div>'
        f'<div class="title">{e(title)}</div><div class="meta">{e(meta)}</div>'
        f'<div class="facts">{facts_html}</div>'
        + (f'<div class="why">{e(why)}</div>' if why else "")
        + f"</div>{link}</div>",
        unsafe_allow_html=True,
    )


def show_item(show):
    day = dt.date.fromisoformat(show["event_date"])
    lead = (
        f'<div class="top">{day:%b}</div><div class="big">{day.day}</div>'
        f'<div class="low">{day:%a}</div>'
    )
    venue, city = show["venue"] or "Venue not listed", show["city"] or ""
    where = venue if city in venue else f"{venue}, {city}"
    meta = " · ".join(p for p in (where, clock(show["local_time"])) if p)
    status = show.get("ticket_status", "unknown")
    facts = [
        (f"Opening for {show['headliner']}" if show["role"] == "support" else "Headliner", ""),
        (status, "hot" if status in NEEDS_ATTENTION else ""),
        (
            f"{show['min_price']:.0f} {show['currency'] or ''}".strip()
            if show["price_known"]
            else "Price not listed",
            "",
        ),
    ]
    if show.get("breakout_score") is not None:
        facts.append((f"Breakout {show['breakout_score']:.0f}", ""))
    why = show.get("why") or ""
    if show["role"] == "support":  # already in the facts line
        why = why.removeprefix(f"opening for {show['headliner']}").removeprefix("; ")
    if show.get("ticket_note") and status != "on sale":
        why = f"{why}. {show['ticket_note']}" if why else show["ticket_note"]
    item(lead, show["artist_name"], meta, facts, why, "Tickets", show["ticket_url"])


def artist_item(rank, artist):
    facts = [
        (f"Breakout {artist['breakout_score']:.0f}", "hot"),
        (f"Listeners {pct(artist['listeners_7d_growth_pct'])} / 7 days", ""),
    ]
    if artist.get("your_plays"):
        facts.append((f"You've played them {artist['your_plays']}x", ""))
    meta = f"{listeners(artist['lastfm_listeners'])} · {artist['genres'] or 'no tags'}"
    url = f"https://www.last.fm/music/{quote_plus(artist['name'])}"
    lead = f'<div class="top">No.</div><div class="big">{rank:02d}</div>'
    item(lead, artist["name"], meta, facts, artist["because"], "Listen", url)


def table(rows, columns):
    """A plain ranked list. columns: (header, key, numeric)."""
    head = "".join(f"<th>{e(h)}</th>" for h, _, _ in columns)
    body = "".join(
        "<tr>"
        + "".join(f'<td class="{"n" if num else ""}">{e(str(r[k]))}</td>' for _, k, num in columns)
        + "</tr>"
        for r in rows
    )
    st.markdown(f'<table class="list"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


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
            for show in result["shows"]:
                show_item(show)
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
            for rank, artist in enumerate(result["artists"], 1):
                artist_item(rank, artist)
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
                    {"n": i, "name": a["name"], "plays": a["your_playcount"],
                     "size": a["size_band"] or "not tracked"}
                    for i, a in enumerate(profile["top_artists"], 1)
                ]  # fmt: skip
                table(rows, [("#", "n", True), ("Artist", "name", False),
                             ("Plays", "plays", True), ("Listeners", "size", False)])  # fmt: skip
            with right:
                st.subheader("Your artists breaking now")
                st.caption("Rising, under 1M listeners. Smaller and more-played rank higher.")
                rows = [
                    {"n": i, "name": a["name"], "score": f"{a['breakout_score']:.0f}",
                     "size": listeners(a["lastfm_listeners"]), "plays": a["your_playcount"]}
                    for i, a in enumerate(profile["your_artists_breaking"], 1)
                ]  # fmt: skip
                if rows:
                    table(rows, [("#", "n", True), ("Artist", "name", False),
                                 ("Breakout", "score", True), ("Size", "size", False),
                                 ("Your plays", "plays", True)])  # fmt: skip
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
            {"n": i, "name": a["name"], "score": f"{a['breakout_score']:.0f}",
             "size": a["size_band"], "growth": pct(a["listeners_7d_growth_pct"]),
             "genres": a["genres"] or ""}
            for i, a in enumerate(ranked["artists"], 1)
        ]  # fmt: skip
        table(rows, [("#", "n", True), ("Artist", "name", False), ("Breakout", "score", True),
                     ("Size", "size", False), ("Listeners, 7 days", "growth", True),
                     ("Genres", "genres", False)])  # fmt: skip
        notes(ranked)
    st.subheader(f"Rising artists playing {city}")
    shows = safe(rising_shows, city, country)
    if shows:
        for show in with_ticket_status(shows)["shows"]:
            show_item(show)
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
