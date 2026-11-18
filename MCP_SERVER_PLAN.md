# Plan: A&R Radar MCP Server ("walkabout" over our own data)

**Audience:** the coding agent working in this repo (and Paul, who must be able to explain every line).
**Goal:** expose A&R Radar's gold/silver tables to Claude through a small, read-only MCP server, so an agent can answer scouting questions like "who's breaking in indie pop in Canada?" or "rising artists with cheap Toronto shows this month?"
**Budget:** $0. No paid APIs, no paid cloud, no LLM API keys. Everything runs locally.
**Time box:** ~1.5–2 hours of agent work. Stop at the "Done when" list; do not gold-plate.

---

## 0. Ground rules for the agent

1. **Discover before you build.** The table/column names below come from the original design doc and may not match the repo. Step 1 is to find the real ones. Never invent columns.
2. **Read-only, always.** The server must not be able to write, drop, or modify anything. No raw SQL tool exposed to the model.
3. **Small, readable code.** One server file under ~250 lines, plus a data-access module and tests. Prefer plain Python over clever abstractions. Add short comments explaining *why*, not *what*.
4. **Stop and ask Paul** if: the gold tables can't be found, the data needs credentials that aren't already configured, or anything would cost money.
5. After each step, write 2–3 sentences in `mcp/NOTES.md` saying what you did and any decision you made, so Paul can review it.

---

## 1. Architecture (and why it's $0)

```
Databricks Free Edition (gold/silver tables)
        │  one-off export script (manual re-run when data refreshes)
        ▼
mcp/data/ar_radar.duckdb   ← local snapshot, gitignored
        │  read-only DuckDB connection
        ▼
mcp/server.py  (FastMCP, stdio transport)
        │
        ▼
Claude Code / Claude Desktop (Paul's existing account — no API spend)
```

**Why a local DuckDB snapshot instead of querying Databricks live:**
- $0 and no dependency on the Free Edition SQL warehouse being awake or within fair-use quotas.
- Fast, works offline, makes tests trivial.
- The data refreshes daily at most; a snapshot is fine for a demo.
- Keep a `DATA_SOURCE=databricks` option as a **stretch only** (Section 8). Do not build it now.

**Dependencies (all free, pip):** `mcp` (official Python SDK, includes FastMCP), `duckdb`, `pandas`, `pyarrow`, `rapidfuzz` (fuzzy name lookup), `pytest`. For the export step only: `databricks-sql-connector` (uses the existing Free Edition workspace + a personal access token Paul already has or can create for free).

---

## 2. Step 1 — Discover the real schema (≈15 min)

1. Search the repo for the gold/silver table definitions (look for `artist_signals`, `artist_daily`, `events`, `upcoming_shows`, `scout_brief`, Asset Bundle YAML, notebooks, SQL files).
2. Write `mcp/SCHEMA.md` listing, for each table we'll use: full name (`catalog.schema.table`), columns + types, grain (one row per what?), and how tables join (expected key: MusicBrainz ID `mbid`; events may join on artist name via the fuzzy-match output).
3. Expected tables (confirm or correct):
   | Purpose | Expected table | Key columns we need |
   |---|---|---|
   | Ranking | gold `artist_signals` | mbid, name, breakout score, 7d/14d listener growth, size band, country, tags/genre, indie flag (if present) |
   | History | silver `artist_daily` | mbid, date, lastfm_listeners, lastfm_playcount |
   | Shows | gold `upcoming_shows` (or silver `events`) | artist/mbid, venue, city, event date, min/max price |
   | Briefs | scout briefs table | mbid, brief text, generated date |
4. Also find how the **breakout score** and **size band** are defined (code or docs) and copy the plain-English definition into `SCHEMA.md`. It becomes an MCP resource later.

**Checkpoint:** if any of the four tables doesn't exist, drop the tool that depends on it and note it. Do not fabricate data.

---

## 3. Step 2 — Export script (≈15 min)

File: `mcp/export_snapshot.py`

- Reads Databricks connection settings from env vars (`DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`) — never hard-code secrets. Add `mcp/.env.example` with blank values.
- For each table in `SCHEMA.md`: `SELECT` the needed columns only (artist_daily: last 30 days only, to keep it small), fetch as Arrow/pandas, write into `mcp/data/ar_radar.duckdb` with the same table names (short names, no catalog prefix).
- Writes a tiny `snapshot_meta` table: export timestamp, row counts per table.
- **Fallback if Databricks access is awkward:** accept a folder of Parquet/CSV files (`--from-files path/`) that Paul exports manually from the Databricks UI. Either path produces the same DuckDB file.
- Add `mcp/data/` to `.gitignore`.

---

## 4. Step 3 — Data-access layer (≈20 min)

File: `mcp/ar_data.py` — plain functions, no MCP code here (so it's testable on its own).

- `connect(path)` → `duckdb.connect(path, read_only=True)`.
- All queries use **parameter binding** (`?`), never f-strings with user input.
- Every function enforces a hard `limit` cap (max 50 rows) and returns `list[dict]` with JSON-safe values (dates → ISO strings, round floats to 3 dp).
- Functions:
  1. `top_breakout_artists(country=None, genre=None, size_band=None, limit=10)`
     - Filters are optional and case-insensitive. Genre matches against tags (`ILIKE '%indie pop%'` or list contains — depends on schema).
     - Returns: name, mbid, breakout_score, growth_7d, growth_14d, listeners (latest), size_band, country, top 3 tags.
  2. `find_artist(name)`
     - Exact case-insensitive match first; else `rapidfuzz` against all names, return best match if score ≥ 85, plus up to 3 alternatives. If no match ≥ 85, return the alternatives and `matched: false` — don't guess.
  3. `artist_profile(name)`
     - Uses `find_artist`, then returns: signals row, last 30 days of listeners (date, value) for a trend, latest scout brief if one exists, and upcoming shows for that artist.
  4. `upcoming_shows(city=None, max_price=None, days_ahead=30, min_breakout_score=None, genre=None, limit=20)`
     - Only future events within `days_ahead` of the snapshot date (use snapshot date, not today, so results are stable).
     - Joined to signals, sorted by breakout score desc. Rows with no price are included but flagged `price_known: false` (don't silently drop them; don't treat missing as $0).
  5. `snapshot_info()` → export timestamp + row counts.

---

## 5. Step 4 — MCP server (≈20 min)

File: `mcp/server.py` using `from mcp.server.fastmcp import FastMCP`, transport **stdio**.

**Tools** (thin wrappers over `ar_data`; docstrings matter — the model reads them to decide which tool to call):

| Tool | Docstring should say |
|---|---|
| `top_breakout_artists` | Ranks artists by breakout score (fast growth relative to audience size). Optional filters: country (ISO code or name), genre tag, size band. Use for "who's breaking / rising / trending". |
| `artist_profile` | Full picture of one artist: signals, 30-day listener trend, scout brief, upcoming shows. Fuzzy-matches the name; if `matched` is false, ask the user which alternative they meant. |
| `upcoming_shows` | Upcoming concerts by rising artists. Filters: city, max ticket price, days ahead, genre, minimum breakout score. Use for "cheap shows near me / who should I go see". |
| `data_status` | When the data was exported and how many rows exist. Use when the user asks how fresh the data is, or before making claims about "this week". |

**Resources** (static context the model can read):
- `ar-radar://definitions` — plain-English definitions of breakout score, size bands, growth windows, and the indie heuristic (from `SCHEMA.md`).
- `ar-radar://limitations` — Last.fm user-base skew, Ticketmaster misses many small venues, short history (so this surfaces growth, it does not predict hits), indie flag is a heuristic.

**Prompt** (optional, 5 lines): `scout_report(genre, country)` — instructs the model to call `top_breakout_artists`, then `artist_profile` on the top 3, then write a short brief citing the numbers and the limitations resource.

**Error handling:** tools return `{"error": "...", "hint": "..."}` for bad input (unknown country, negative price) rather than raising. Log to **stderr only** (stdout is the MCP channel — printing to stdout breaks stdio transport).

---

## 6. Step 5 — Tests (≈15 min)

File: `mcp/tests/test_ar_data.py`

- Build a tiny in-memory/temporary DuckDB fixture (~8 artists, ~6 events, a few daily rows, one brief) — **don't** depend on the real snapshot.
- Test:
  - filters work (country, genre, size band) and `limit` cap is enforced (asking for 500 returns ≤ 50)
  - sorting is by breakout score desc
  - fuzzy match: exact, typo ("Phobe Bridgers" → match), and no-match returns `matched: false` with alternatives
  - `upcoming_shows` excludes past events, respects `max_price`, keeps unknown-price rows flagged
  - SQL-injection-shaped input (`"x'; DROP TABLE artist_signals; --"`) returns no rows and the table still exists
  - connection is read-only (a write attempt raises)
- Add one smoke test that starts the FastMCP server object and lists its tools (names match the table above).
- Hook into the existing pytest/GitHub Actions setup if trivial; otherwise leave it runnable with `pytest mcp/tests`.

---

## 7. Step 6 — Wire it up + mini eval (≈15 min)

1. **Claude Code:** add a project-scoped config so it's one command for Paul:
   ```
   claude mcp add ar-radar -- python mcp/server.py
   ```
   (or a `.mcp.json` at repo root with the same command). Document both in the README section.
2. **Claude Desktop (alternative):** add the equivalent `mcpServers` entry to `claude_desktop_config.json`; put a copy-paste snippet in the README.
3. **MCP Inspector** for manual debugging (free): `npx @modelcontextprotocol/inspector python mcp/server.py`.
4. **Mini eval:** `mcp/eval_questions.md` with 5 questions and the expected answer derived by running SQL directly against the snapshot:
   1. Top 5 breakout artists in Canada right now?
   2. Who's rising in indie pop (any country)?
   3. Tell me about <a real mid-tier artist from the data> — is their growth real or a one-day spike?
   4. Rising artists with shows in Toronto under $30 in the next 30 days?
   5. How fresh is this data?
   Paul runs each through Claude with the server connected and marks pass/fail. Record results in the file. (This mirrors MoE's "set an acceptance bar, then test" approach — that's the point.)

---

## 8. Out of scope for now (stretch, later)

- `DATA_SOURCE=databricks` live mode via `databricks-sql-connector`.
- Claude Agent SDK script doing a multi-step question (needs an API key → not $0 unless run through Claude Code; defer).
- Remote deployment (HTTP transport, AWS). Local stdio is enough for the demo.
- Similarity/"if you like X" tool.

---

## 9. README section (≈10 min)

Add `## Agent access (MCP)` to the main README:
- One paragraph: why (applying the "walkabout" idea — give an agent read-only access to a system and let it explore — to my own pipeline).
- Diagram from Section 1.
- Setup: export snapshot → `claude mcp add ...` → example questions.
- Tool list (one line each), limitations, and the eval pass rate.

---

## 10. Done when

- [ ] `mcp/SCHEMA.md` reflects the real tables
- [ ] `python mcp/export_snapshot.py` (or `--from-files`) produces `mcp/data/ar_radar.duckdb`
- [ ] `pytest mcp/tests` passes
- [ ] Server shows 4 tools + 2 resources in MCP Inspector
- [ ] Claude Code answers all 5 eval questions with the server connected; results recorded
- [ ] README section written
- [ ] `mcp/NOTES.md` has a short log of decisions
- [ ] Total cost: $0

---

## 11. For Paul: be able to explain these (interview prep)

The agent should add a short "How it works" list at the top of `mcp/NOTES.md` covering:
1. What MCP is in one sentence, and what stdio transport means (client launches the server as a subprocess; JSON-RPC over stdin/stdout — hence no printing to stdout).
2. Why tools are narrow and typed instead of one "run SQL" tool (safety, and the model picks better with clear docstrings).
3. Why a read-only DuckDB snapshot (cost, speed, reproducibility; trade-off is staleness, surfaced via `data_status`).
4. How fuzzy matching avoids the false-merge problem from the main pipeline (threshold + "ask the user" when unsure).
5. How the eval set works and what the pass rate was.
