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
- **Engineering:** Databricks Asset Bundles with separate dev and prod, 62 pytest tests, GitHub Actions CI, and quality checks that fail the job and send an alert.

## Limitations

- Last.fm only counts people who log their listening there, and its users skew toward certain demographics and genres. It's one signal, not the whole picture.
- Ticketmaster misses many of the smallest venues, and only about 12% of matched shows list a price.
- The history is short, so the score hasn't been backtested. It surfaces fast growth; it does not predict success.
- Signing status comes from user-written bios. "No label mentioned" does not mean unsigned.
- Personal project. Not affiliated with any record label.

## Status

- Live: Daily pipeline, breakout score, shows, scout briefs, Aldous
- Future: Streamlit app (Shows, Ask Aldous, Radar and Artist tabs) and a demo video
- Future: Backtest of the breakout score once there are a few weeks/months of history

## Repo layout

```
src/anr_radar/     collectors, API clients, scoring, matching, transforms, briefs, quality checks
resources/         Databricks job definitions (Asset Bundle)
genie/             Aldous configuration and test log
docs/              technical summary and data notes
tests/             pytest suite
```

## Running it

Requires a Databricks workspace (Free Edition works), a Last.fm API key and a Ticketmaster Discovery API key.

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env                  # add your API keys
pytest                                # 62 tests
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
