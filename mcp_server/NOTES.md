# MCP server: notes and decision log

## How it works (be able to explain these)

1. **What MCP is.** The Model Context Protocol is a standard way for an AI client (Claude Code,
   Claude Desktop, …) to discover and call tools, read resources and use prompt templates that a
   separate program, the *server*, exposes. With the **stdio transport** the client launches
   the server as a subprocess and they exchange JSON-RPC messages over its stdin/stdout. That's
   why `server.py` logs only to stderr: one stray `print()` would corrupt the protocol stream.
2. **Narrow, typed tools instead of one "run SQL" tool.** Safety: the model can only ask the
   questions we designed, user text only ever reaches SQL as bound `?` parameters, and the DB
   connection is opened read-only, so even a bug can't write. Quality: the model picks tools
   from their names, typed arguments and docstrings, so a clear `upcoming_shows(city,
   max_price)` gets called correctly far more often than free-form SQL against unfamiliar
   tables. The domain rules (exclude alias pages, exclude unscored artists, keep unknown
   prices, superstars off by default) live in code once, instead of in a prompt the model might
   ignore. This is the same lesson as Aldous's "label" bug: put rules in structure, not prose.
3. **Why a read-only DuckDB snapshot.** $0 and offline (no warehouse to wake up, no Free
   Edition quota), fast (milliseconds), reproducible ("today" is the snapshot's `as_of_date`,
   so the same question gives the same answer), and trivial to test. The trade-off is
   staleness, which is surfaced through the `data_status` tool and the server instructions.
4. **Fuzzy matching without false merges.** `find_artist` reuses the pipeline's own
   `normalize()` and thresholds from `matching/names.py`: exact normalized name first; a fuzzy
   match is accepted only at ≥ 95 similarity, only for names of 5+ characters, and only if no
   second candidate is also ≥ 95. Otherwise it returns `matched: false` with the top 3
   alternatives, and the tool docstring tells the model to ask the user. So "Phobe Bridgers"
   resolves, but "Spon" never silently becomes Spoon or Spoons.
5. **Personalization without a login.** Last.fm listening is public by username, so the
   personal tools need only the free API key the pipeline already uses. Your top artists are
   matched to ours by MusicBrainz ID or exact name (no fuzzy merges). Affinity is
   0.7 × similar-artist score (Last.fm's "similar" graph, already collected while building the
   pool) + 0.3 × tag overlap, and every result carries a `because`. Listening data stays in
   memory (30 min) and the logs show a hash, never the username. Login (Last.fm's token
   flow) is a later phase: it proves account ownership, which only matters once the
   server is hosted for other people.
6. **The eval set.** `eval_questions.md` has 5 questions. Each expected answer was derived by
   running SQL directly against the snapshot *before* asking Claude, so it's an acceptance bar
   set in advance, not a grade invented afterwards. Each question ran in a fresh Claude Code
   session with only this server's tools (no shell or file access). **Result: 5/5**, after
   fixing a stale docstring that the first run repeated to the user.

## Files

| File | What |
|---|---|
| `SCHEMA.md` | the real tables/columns, and where the original plan's assumptions were wrong |
| `export_snapshot.py` | Databricks (or downloaded files) → `data/ar_radar.duckdb` |
| `ar_data.py` | all queries; plain functions, no MCP code |
| `server.py` | MCP layer: 4 tools, 2 resources, 1 prompt; thin wrappers over `ar_data` |
| `context/*.md` | text served as the `definitions` and `limitations` resources |
| `tests/` | 41 tests on a hand-made fixture snapshot (never the real data) |
| `eval_questions.md` | 5-question acceptance test with expected answers and results |

## Decision log

**Step 1, schema.** Read the table definitions from `transform/gold.py`, `briefs/generate.py`
and the Genie instructions rather than trusting the plan. Four corrections: the join key is
`artist_key`, not `mbid`; there is no country column (country comes from `chart_countries` or
nationality tags, the same rule Aldous uses); there is no indie flag; and history comes from
gold `artist_growth_history`. All four planned tables exist, so no tool was dropped.

**Folder name.** The plan said `mcp/`, but that is also the name of the MCP SDK package, so
`import mcp` inside it is confusing at best and shadowed at worst. Renamed to `mcp_server/`.

**SDK version.** `pip install mcp` now gives v2, where `FastMCP` was renamed `MCPServer`
(`from mcp.server.mcpserver import MCPServer`). The decorator API is the same. Tools return
`dict[str, Any]` (not bare `dict`) so v2 also sends machine-readable `structuredContent`.

**Step 2, export.** Uses the Databricks SDK's Statement Execution API with the existing
`databricks auth login` profile, instead of `databricks-sql-connector` plus a personal access
token: one less dependency and no long-lived secret. Results come back as Arrow, so types
(dates, arrays) survive. The file is written to a temp path and swapped in atomically, and
`--from-files` accepts Parquet/CSV downloads from the UI (CSV arrays are re-parsed from JSON).
`mcp_server/data/` was already covered by the root `.gitignore` (`data/`).

**Step 3, data layer.** Each call opens its own read-only connection: cheap for DuckDB, avoids
sharing a connection across threads, and a fresh export is picked up without restarting the
server. Functions return a small envelope (`as_of_date`, `filters`, `count`, rows, `notes`)
rather than a bare list, so the model can see which filters were applied and any caveats.
Added a `trend.pattern` (steady / concentrated / flat) to `artist_profile` because "is the
growth real or a one-day spike?" is the question a scout actually asks. Unknown countries,
cities and size bands are errors with a hint listing valid values, rather than silent empty
results.

**Step 4, server.** Tools are marked `readOnlyHint` (an MCP annotation) so clients know they're
safe. The server sends short `instructions` (use `data_status` before saying "this week";
never predict success). Added an optional `--http` flag (streamable HTTP) because it's 3 lines
and it's the hook for a later cloud deployment; stdio stays the default.

**Step 5, tests.** The fixture is built through the real `write_snapshot`, so the writer is
tested too. It includes the cases the pipeline actually hit: an alias page (JAŸ-Z), look-alike
names (Spoon/Spoons), an unscored artist, a provisional artist, a show with no price, a past
show and an accented city. Wired into the existing pytest config, so CI runs them.
DuckDB 2.0 note: a result must be fully fetched before the connection does other work, so the
code uses `fetchall()` everywhere.

**Step 6, wiring.** `.mcp.json` at the repo root registers the server for Claude Code
(project scope, approved on first launch). Verified end to end with an SDK stdio client:
4 tools, 2 resources, 1 prompt.

**Step 6b, real data (2026-11-17).** The export pulled 5,810 artists, 141k history rows, 876
shows and 50 briefs in about 20 seconds through the Serverless Starter Warehouse. Real data
exposed four things the fixture didn't:
- *The snapshot is 30 days old.* The prod daily job last ran on 2026-10-18 (all runs
  succeeded, then none were scheduled; most likely Free Edition idling after inactivity). Ranking
  "as of the snapshot" is fine, but a show window starting at the snapshot date would list gigs
  that already happened. Show windows now start at the later of the snapshot date and today,
  and every result carries a "data is N days old" note when it's more than 2 days old.
- *Two spellings of one city.* Ticketmaster uses both `Montreal` (291 rows) and `Montréal`
  (10). The city filter now keeps every spelling that folds to the same text.
- *Timezone-aware timestamps.* `generated_at` arrives as TIMESTAMPTZ, and DuckDB needs `pytz`
  to return those to Python. The export now stores them as plain UTC rather than adding a
  dependency. The fixture is now tz-aware so the test covers it.
- *Cheap shows crowded out.* For Toronto under $30, 60 shows match, but only 7 list a price,
  and the top 10 by score were almost all unpriced. Results now include `total_matches` and
  `priced_matches`, and there's a `require_price` option. In the eval, Claude used it unprompted.

**Step 6c, eval.** 5/5. See `eval_questions.md`. Lesson: the model trusts docstrings
completely, so a docstring that's out of date is a bug that reaches the user.

**Step 7, personalization (2026-11-18, plan: username first, login later).**
- *Client.* `LastFmClient` gained `user_info` and `user_top_artists`, reusing its throttling,
  retries and `NotFound`.
- *Taste graph.* The export now includes silver `artist_similar` (57,660 edges), so the
  graph costs no new API calls. Only for heavy seeds we don't track (up to 10) does it ask
  Last.fm for their similar artists live.
- *Code layout.* All logic is in `personal.py`, as plain functions taking a client and the
  snapshot. Tests use a fake client plus one `responses`-mocked end-to-end run.
- *Tool flags.* The three tools are marked `openWorldHint` because, unlike the others, they
  call an external service.
- *Username.* It comes from the tool argument, else `LASTFM_USERNAME` (from `.env`, which
  `get_secret` already loads), else an "ask the user" hint. That replaced the plan's
  `.mcp.json` env idea: a missing variable there can break startup, while `.env` was
  already the project's convention. Usernames are validated against Last.fm's rules before
  any request.

**Step 7b, the pipeline came back.** The scheduled daily job ran again on 2026-11-18, after
the workspace became active again. The month-long gap broke two assumptions:
- *History export.* Exporting "the last 30 calendar days" held a single day, so it now
  exports the last 30 *snapshot* days.
- *Trend labels.* The trend summary now spreads a change across a gap over the gap's days and
  lists the gaps. Otherwise the Oct 18 → Nov 18 catch-up looked like a one-day spike.

The pipeline's own "7-day" growth is currently measured over 31 days, so every answer that
cites growth now carries a note saying so. This lasts until a week of daily runs has built up.

**Step 7c, personal eval (3 questions on Paul's account).** 2/3 at first, 3/3 after one fix
(see `eval_questions.md`). P3 was regraded after the run: my answer key assumed "this month"
meant the next 30 days, and Paul confirmed it means the rest of the calendar month, which is
what Claude did. I added no rule for it, because a prompt rule per time phrase is overfitting. Real data caught three bugs the
fixture didn't:
- a top-100 cutoff that hid every matching Toronto show;
- edges to untracked artists that crashed `shows_for_me`;
- superstars listed as "your artists breaking".

The eval caught a fourth: the model reported 0.21% growth as 21% in one answer, even though it
read the same field correctly in another. The fix was to state the units where the model reads
them (server instructions, tool description). Lesson, again: the model takes field names and
docstrings literally, so units and definitions have to be in the text it reads.

**Step 7d, your whole library counts (Paul's call).** Taste weights still come from the top 50,
but "artists you listen to" now means the tracked artists in the all-time top 500 (one call,
`limit=500`). These artists come first in `shows_for_me`, are excluded from discovery, and
feed "your artists breaking". The reason: for concert tickets, a show by your #180 artist is
exactly the news you'd want, and deeper-library artists are the smaller, cheaper ones. On
Paul's data, 283 library artists are tracked and Toronto matches this month went from 2 to 7.

**Step 7e, ranking shows by what the question asks (Paul's call).** First try: rank your
artists' shows 50/50 by plays and breakout. It put Doja Cat (68 plays, arena) well above
Angela Autumn (1 play, $27.75, small room). But Paul's view is that both are useful finds: one
is a favourite you may not know is touring, the other a cheap show by an emerging act.
- *Default weights.* The fix added the project's original idea as a factor: an emerging
  artist (under 500k listeners) means a small room and a cheaper ticket. Default for your
  artists: fan .4, breakout .4, emerging .2. Angela now edges Doja (0.604 vs 0.581) without
  tuning for that pair.
- *Wording becomes arguments.* The server never sees the question, so `shows_for_me` takes
  `who` (any / mine / new) and `prefer_cheap`. The tool description maps example wordings
  to settings, and the weights are a fixed table in `personal.py` (`SHOW_WEIGHTS`). The
  model interprets; the code ranks reproducibly. Each show also returns `score_parts`, so any
  ranking can be explained.
- *Results.* On Paul's data, "cheap show by an artist I listen to" puts Angela Autumn first
  (the only one with a listed price).
- *Known gap.* "A new artist that's cheap" finds only 2 Toronto shows: new candidates are
  limited to artists linked to your taste in the similar-artist graph, and neither of those
  lists a price.

**Step 7f, no hard categories (Paul's call).** "Yours" vs "new" was a yes/no split on the
top 500, so an artist played once in passing counted as fully yours. Now:
- *Familiarity is a dial.* It's log plays against your most-played artist (1 play ≈ 0.1).
- *`who` re-weights, never filters.* It shifts weight between familiarity and novelty.
- *Discovery uses the dial too.* It multiplies by (1 − familiarity)², so barely-played artists
  can still be suggested and favourites can't.

One over-correction was caught on real data. Admitting tag-only matches as show candidates
put loosely related rising acts (taste link ~0.07, from sharing "pop"/"indie") above
favourites like Doja Cat. Show candidates are back to "played at all, or similar-artist
linked", which is Paul's own definition. Results on Paul's data (Toronto, 30 days):
- default: Angela Autumn 0.604 just above Doja Cat 0.581;
- "new": Angela is first (1 play counts as new);
- "mine + cheap": Angela is first (the only listed price).

The candidate pool is small (7 shows), which is a coverage limit of the similar-artist graph.

**Step 8, headliner vs. opener and live ticket status (Paul's request).** Two parts:
- *Lineups.* Anish Kumar's "Toronto show" is a support slot on Bonobo's tour. The match was
  right, but the answer never said so. The export now includes silver `event_performers`, and
  every show carries `role`, `headliner` and `lineup`. Openers' `why` starts "opening for X",
  and `shows_for_me` multiplies their score by 0.8.
- *Sold out.* Ticketmaster's public Discovery API has no sold-out field. Its status codes are
  onsale, offsale, canceled, postponed and rescheduled, and `offsale` is undefined. The
  partner Inventory Status API returned 401 for our key. So `tickets.py` makes one batched
  live request per answer (30 events in about 0.5 s, cached 10 min) and labels status from
  the code plus the public sale window. "Off sale" inside the window means "often sold out,
  unconfirmed", and the model is told never to say "sold out". Canceled shows are dropped.
  If Ticketmaster is unreachable, status is "unknown" and the answer still works.
- *Pipeline bug found on the way.* `gold.upcoming_shows` filtered `status <> 'cancelled'`,
  but Ticketmaster spells it "canceled", so canceled shows could reach the gold table. Fixed in
  `transform/gold.py`; it needs a bundle deploy to take effect. The live check covers it
  meanwhile.

**Step 9, local web app (`app/`, Streamlit).** Streamlit was chosen because it's pure Python
on top of the existing functions and was already on the README's roadmap. Choices:
- *No AI in the main tabs.* Shows for me, Discover, My taste and Rising now call
  `personal.py`, `ar_data.py` and `tickets.py` directly: no LLM, $0, about 7 s on first load
  (Last.fm plus ranking) and 0.1 s after, because results are cached for 10 minutes.
- *Chat at $0.* It runs `claude -p` with the ar-radar MCP server on Paul's subscription,
  with built-in tools disabled (`--tools ""`), so browser input can only reach the read-only
  tools. It streams tool progress into the UI and continues the conversation with
  `--resume`. A real test took 15 s for the first answer and 5 s for a follow-up.
- *Local only.* `.streamlit/config.toml` binds to localhost (the chat spends the
  subscription) and turns off Streamlit's usage telemetry.
- *Hosting later.* Swapping `chat.ask` for the Claude API is the only change needed.
- *Shared setup.* The Last.fm client and Ticketmaster key now live in `personal.client()` and
  `tickets.api_key()`, used by both the server and the app.
- *Tests.* The chat runner is tested with a fake process; the whole app is tested headless
  (Streamlit AppTest) on the fixture snapshot with fake Last.fm and Ticketmaster.

**Step 10, any city, a less generic look, and "breaking" means small.**
- *Any city without Databricks.* A new city doesn't need a pipeline run.
  `live_events.for_city` does one Ticketmaster search (Chicago 2 s, London 5 s, Berlin under
  1 s; cached an hour) and matches performers with the pipeline's own `match_performers`.
  `ar_data.upcoming_shows(live=...)` then runs the same SQL over the live tables, shaped like
  the snapshot by joining `artist_signals`, so ranking, roles and filters are identical.
  The search results also seed the ticket-status cache, so there's no second request.
  - The trade-off: only tracked artists can be ranked, and the pipeline only adds local acts
    for Toronto and Montréal. A new city shows rising artists touring through, not its scene.
  - Adding a city to `config.EVENT_CITIES` gives it the full treatment: a few Ticketmaster
    calls plus Last.fm lookups for its new performers in the daily run.
  - The MCP tools take any city plus an optional `country_code` for ambiguous names
    (London, GB).
- *Your artists breaking.* Paul's list was all 500k-2M artists (Omar Apollo, Car Seat
  Headrest): growing fast for their size, but long established. Now 1M+ listeners is out,
  and the rest rank by breakout × smallness (log scale) × (0.25 + 0.75 × familiarity). His
  list became Anish Kumar, Angela Autumn, BUNT., Gus Dapperton and Rico Nasty, and a note
  counts the 82 established ones left out.
- *Design.* Paul found the first version looked AI-generated. Changes:
  - no emojis (a test checks);
  - IBM Plex Sans and Mono with Archivo Narrow headings;
  - warm paper with ink and one vermilion accent, square corners and a dark sidebar;
  - shows laid out as gig listings with a date block, plain ranked tables instead of data
    grids, and no Deploy toolbar.
