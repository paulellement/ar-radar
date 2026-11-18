# A&R Radar

**Find fast growing artists while their concert tickets are still cheap**

People want to go to more small shows because they're cheaper, but it's hard to tell who's actually good. A&R scouts have the same problem for finding artists.

A&R Radar does the checking automatically every day. It tracks 5,700+ artists and ~940 upcoming Toronto and Montreal concerts, flags the artists growing unusually fast *compared with artists of a similar size*, writes a one-page scout brief for the top ones, and lets you ask questions in plain English through **Aldous**, a chat assistant.

## Ask Aldous

Real answers from the test runs (Genie on Databricks, reading the pipeline's tables):

**"Which rising artists mention a record label in their brief?"**
> There are **6 rising artists** who mention a record label in their brief: **Gilla Band** (Rough Trade), **Tuide** (ABD), **CHXRRY** (XO Records), **Evan** (BELIFT LAB), **Kiefer** (Stones Throw Records) and **After** (Skullr Records).

**"What shows are happening in Montreal this weekend with rising artists?"**
> There are **3 shows**: **she's green** (dream pop, shoegaze) at Bar le Ritz PDB, **Graham Barham** (country) at Petit Campus, and **dublon** (house) at Bar le Ritz PDB. None list a ticket price.

**"Any good shows near me?"**
> Can you specify which city you are in? I can show upcoming shows in Toronto or Montreal.

Every answer shows the SQL behind it. Aldous was tested on 13 questions (10 examples plus 3 edge cases) and scored **12/13 after fixes, up from 10/13**. The test log, including what went wrong and how it was fixed, is in [`genie/instructions.md`](genie/instructions.md).

## What it does

- **Breakout score (0–100):** each artist's recent listener and play growth, compared only with artists of a similar size. A jump from 20k to 26k listeners is more significant than 2M to 2.1M.
- **Shows:** rising artists with upcoming shows in Toronto and Montreal, with venue, date and price if listed on Ticketmaster's API.
- **Scout briefs:** a weekly one-page brief for the top 50 artists under 2M listeners, written by an LLM using only facts from the pipeline. It always states signing status, quoted from the artist's bio.
- **Aldous:** ask questions like *"who's breaking in indie pop this month?"* instead of filtering tables.

## How it works

```
Ticketmaster API ─┐                    ┌─ bronze (raw) ─ silver (clean) ─ gold (scores, shows, briefs)
                  ├─ Python collectors ┤      Delta tables in Unity Catalog                │
Last.fm API ──────┘   (daily job)      └───────────────────────────────────────────────────┴─→ Aldous (Genie)
```

- **Daily Databricks Job:** pulls upcoming concerts, snapshots every tracked artist on Last.fm, then rebuilds the bronze → silver → gold tables and runs data-quality checks. Last.fm only reports current totals, so these daily snapshots *are* the history.
- **Breakout score:** log growth over 7 and 14 days, turned into a robust z-score (median/MAD) within listener-size bands, soft-capped, and combined into a percentile.
- **Scout briefs:** `ai_query` on a Databricks-hosted model (gpt-oss-120b, Llama 3.3 as fallback), with each brief's exact prompt stored so every claim can be checked.
- **Engineering:** Databricks Asset Bundles with separate dev and prod, 154 pytest tests, GitHub Actions CI, and quality checks that fail the job and send an alert.

## Agent access (MCP)

Aldous answers questions inside Databricks. To let *any* agent explore the pipeline's output, I wrapped the gold tables in a small, read-only [MCP](https://modelcontextprotocol.io) server. Claude Code or Claude Desktop can then call scouting tools directly: *"who's breaking in indie pop in Canada?"*, *"rising artists with cheap Toronto shows this month?"*

```
Databricks gold tables ──export_snapshot.py──▶ mcp_server/data/ar_radar.duckdb (local, read-only)
                                                        │
                                              mcp_server/server.py (MCP, stdio)
                                                        │
                                              Claude Code / Claude Desktop
```

| Tool | What it does |
|---|---|
| `top_breakout_artists` | ranks by breakout score; filters for country, genre tag, size band |
| `artist_profile` | one artist: signals, 30-day trend (steady vs. one-day spike), scout brief, shows; asks when a name is ambiguous |
| `upcoming_shows` | shows by rising artists (Toronto/Montréal from the snapshot, any other city live); filters for price, date window, genre, minimum score; headliner or opener, plus live ticket status |
| `data_status` | how fresh the snapshot is |
| `my_taste_profile` | your Last.fm taste, and which of *your* artists are breaking now |
| `discover_for_me` | rising artists you don't listen to yet, with "because you play X" |
| `shows_for_me` | upcoming shows: your artists first, then rising artists that match your taste |

Plus two resources (`ar-radar://definitions`, `ar-radar://limitations`) and two prompts (`scout_report`, `my_report`).

**Personalized by Last.fm username.** No login is needed, because Last.fm listening is public. The server reads your top artists live, maps them to tracked artists by MusicBrainz ID or exact name (never fuzzy), and scores taste with Last.fm's similar-artist graph plus tag overlap. Every pick says why. Your listening is held in memory for 30 minutes and never stored or logged. Set `LASTFM_USERNAME` in `.env`, or just tell Claude your username.

**Design choices:** narrow typed tools instead of a "run SQL" tool, bound parameters only, a read-only connection, and the same fuzzy-matching thresholds as the pipeline (it asks rather than guesses). It costs $0: a local DuckDB snapshot, no warehouse and no paid APIs (personal picks and live ticket status use the same free Last.fm and Ticketmaster keys as the pipeline). The reasoning and a decision log are in [`mcp_server/NOTES.md`](mcp_server/NOTES.md); schema notes are in [`mcp_server/SCHEMA.md`](mcp_server/SCHEMA.md).

**Setup**

```bash
uv pip install -e ".[mcp]"
databricks auth login --profile <profile>
python mcp_server/export_snapshot.py --profile <profile>   # or --from-files <folder of .parquet/.csv>
claude                                                     # approve "ar-radar" from .mcp.json
```

Or register it yourself with `claude mcp add ar-radar -- .venv/bin/python mcp_server/server.py`. For Claude Desktop, add this to `claude_desktop_config.json` (absolute paths):

```json
{ "mcpServers": { "ar-radar": {
    "command": "/path/to/repo/.venv/bin/python",
    "args": ["/path/to/repo/mcp_server/server.py"] } } }
```

To debug, run `npx @modelcontextprotocol/inspector .venv/bin/python mcp_server/server.py`.

**Eval: 5/5.** Five scouting questions, with expected answers written in advance from direct SQL and run through Claude Code with only this server available (no shell or file access). Building the eval caught 3 data bugs, and one run caught a stale docstring that the model repeated to the user. A second eval with 3 personal questions on my own Last.fm account scored 2/3 on the first run (Claude read 0.21% growth as 21%) and 3/3 after stating the units in the tool descriptions. Building it also caught 3 bugs. Details are in [`mcp_server/eval_questions.md`](mcp_server/eval_questions.md).

## Web app (local)

`streamlit run app/streamlit_app.py` opens A&R Radar at http://localhost:8501. Enter a Last.fm username, then pick:

- **Shows:** ranked by how well you know the artist, breakout score, emerging size and price. Each show says headliner or opener and has live ticket status.
- **Discover:** rising artists new to you.
- **Your taste:** your top artists and tags, and which of your smaller artists (under 1M listeners) are breaking now.
- **Rising:** the general ranking, plus rising artists playing your city.
- **Ask:** free-form questions.

**Any city.** Toronto and Montréal come from the daily pipeline, which also scores their local acts. Type any other city and the app searches Ticketmaster live (1 to 5 seconds, cached for an hour, well inside the free 5,000 calls a day) and matches the performers to the tracked artists with the pipeline's own matcher. No Databricks run is needed per city. The catch: only already-tracked artists can be ranked, so a new city shows the rising artists touring through it, not its local scene.

Every tab except Ask calls the same Python functions as the MCP server, with no AI and $0. Ask runs Claude Code on your machine with only the read-only A&R Radar tools, so it uses your Claude subscription. The app binds to localhost only.

## Limitations

- Last.fm only counts people who log their listening there, and its users skew toward certain demographics and genres. It's one signal, not the whole picture.
- Ticketmaster misses many of the smallest venues, and only about 12% of matched shows list a price.
- The history is short, so the score hasn't been backtested. It surfaces fast growth; it does not predict success.
- Signing status comes from user-written bios. "No label mentioned" does not mean unsigned.
- Personal project. Not affiliated with any record label.

## Status

- Live: Daily pipeline, breakout score, shows, scout briefs, Aldous
- Live: Read-only MCP server so Claude can query the results, with personal picks by Last.fm username
- Live (local): Streamlit app: enter a Last.fm username for shows, new artists and your taste, plus rising artists and a chat
- Future: demo video
- Future: Backtest of the breakout score once there are a few weeks/months of history

## Repo layout

```
src/anr_radar/     collectors, API clients, scoring, matching, transforms, briefs, quality checks
resources/         Databricks job definitions (Asset Bundle)
genie/             Aldous configuration and test log
mcp_server/        MCP server, snapshot export, tests and eval (agent access)
app/               local Streamlit web app on top of mcp_server/
tests/             pytest suite
```

## Running it

Requires a Databricks workspace (Free Edition works), a Last.fm API key and a Ticketmaster Discovery API key.

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env                  # add your API keys
pytest                                # 154 tests (64 pipeline + 82 MCP + 8 app)
databricks auth login
./scripts/setup_secrets.sh            # stores keys in a Databricks secret scope
# run scripts/setup_uc.sql in the SQL editor, then:
databricks bundle deploy -t prod
databricks bundle run -t prod collector
```

## Use of AI

Claude Code was used to write most of the pipeline code and unit tests, starting
from a detailed problem statement I wrote about finding fast-rising
artists. I set the scope and reviewed the generated code.

The genie assistant was setup by myself on databricks, with claude used to help generate example queries and test prompts.
