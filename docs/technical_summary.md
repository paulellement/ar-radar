# A&R Radar: technical summary

## The one-liner
A daily data pipeline on Databricks that tracks about 5,000 artists' listening data and about 1,000 upcoming Toronto/Montréal concerts. It scores which artists are growing unusually fast for their size, writes LLM scout briefs for the top ones, and exposes everything to a natural-language assistant (Genie) and, next, a Streamlit app. It serves two users: A&R scouts, who want to find artists before they break, and fans, who want good artists playing cheap small shows (the friend's original problem).

## Architecture

```
Ticketmaster API ─┐                    ┌─ bronze (raw VARIANT) ─ silver (typed, deduped) ─ gold (scores, shows, briefs)
                  ├─ Python collectors ┤          Delta tables in Unity Catalog               │
Last.fm API ──────┘   → JSONL in a     └──────────────────────────────────────────────────────┴─→ Genie ("Aldous") / Streamlit app
                        UC Volume
```

**One daily Databricks Job** (06:00 UTC, serverless) runs three steps in order:
1. **Events:** pull upcoming music events from Ticketmaster.
2. **Snapshot:** pull current stats for every tracked artist from Last.fm.
3. **Pipeline:** a separate job, triggered here, that runs bronze → silver → gold → quality checks.

A **weekly job** (Mondays) generates the scout briefs.

## 1. Data collection
- **Why Last.fm and not Spotify?** Spotify's February 2026 API changes removed follower counts, popularity and top tracks, so it's no longer usable for growth signals. Last.fm gives listeners, play counts, tags, bios, similar artists and per-country charts.
- **Last.fm only returns current totals, with no history.** So the core design constraint is collecting a snapshot every day starting on day 0. The daily snapshots *are* the history, and every day of delay is lost data.
- **Building the candidate pool:**
  - Start from the top artists in 22 countries and 21 genre tags.
  - Add one hop of `artist.getSimilar` to reach mid-sized artists.
  - Chart seeds are mostly superstars, so they're capped at 40% of the pool; that pushed the typical similar-artist pick down to about 234k listeners, versus 640k–900k for chart picks.
  - Rebuilt weekly. Existing members are never dropped, because they carry history.
- **Performers of upcoming shows join the pool daily.**
  - Only 18% of Toronto performers were in the chart-based pool; most bands playing Lee's Palace aren't on any chart.
  - The events feed therefore doubles as a source of artists to track, with its own cap.
- **The collectors are plain Python** (`requests`, no Spark) that write JSONL to a Unity Catalog Volume.
  - The same code can run as a Databricks Job or a GitHub Actions cron. The fallback existed because Free Edition restricts outbound internet access, and I didn't know on day 0 whether Last.fm would be reachable.
  - Includes rate limiting (4 requests/second), retries with exponential backoff, and handling for Last.fm's quirk of returning a bare object instead of a one-item list.
  - Re-running a day overwrites that day's files, so reruns are safe.
  - The run fails loudly if more than 10% of lookups error.
- **Ticketmaster collector:** its API won't page past 1,000 results, so busy date ranges are split in half recursively until each fits.

## 2. Medallion layers (PySpark and SQL)
- **Bronze:** each raw JSON line is stored whole in a **`VARIANT`** column, so changes to the API's response shape never break ingestion. Loading is idempotent per day using `replaceWhere`. Each run backfills any missing days and reloads the 2 most recent.
- **Silver** parses the VARIANT paths into typed tables:
  - `artist_daily`, `artist_profile`, `chart_daily`, `artist_similar`
  - `events`, `event_performers` (billing order via `variant_explode`), `event_artist_match`
  - A `event_match_review` view for uncertain matches.
  - Handles Last.fm returning a list, a single object, or `""` for the same field.
- **Gold:**
  - `artist_signals`: one row per artist.
  - `artist_growth_history`: daily totals, for sparklines.
  - `upcoming_shows`: rising artists joined to their shows.
  - `scout_briefs`: the LLM briefs.
  - Every gold table and column has a plain-English **Unity Catalog comment**. Genie reads them to understand the data.
- **Dev and prod are fully separated.** The Asset Bundle targets write to `dev_anr_*` or `anr_*` schemas, so I can test on real data without touching production.

## 3. The breakout score (the core logic, pure pandas and unit-tested)
- **Growth is a log rate** over up to 7 or 14 days: `ln(now/then)/days`. It works from day 2 onward, and rows with less than 7 days of history are flagged `is_provisional`, so there's usable output before a full week exists.
- **The size adjustment is the key idea.** Growth is turned into a **robust z-score within each listener band** (<10k, 10k–50k, 50k–500k, 500k–2M, 2M+), using median and MAD instead of mean and standard deviation, so it's resistant to outliers. That's how 20k→26k outranks 2M→2.1M: each artist is compared only with peers of similar size.
- **Soft clipping.** The z-scores are capped with `5·tanh(z/5)` instead of a hard clip at ±5. The first version used a hard clip, and a test caught standout artists tying for #1. The soft version still bounds outliers but keeps them in order.
- **Weighted composite:**

  | Component | Weight |
  |---|---|
  | Listener growth | 45% |
  | Play growth | 25% |
  | New country chart entries | 15% |
  | YouTube | 15%, not built yet; its weight is spread over the others |

  The composite becomes a **0–100 percentile**, which is easier for users and Genie to interpret.

## 4. Data-quality findings (the best interview stories)
1. **Last.fm serves cached stats.** 96% of artists looked unchanged between the first two snapshots.
   - Growth was measured in 7- and 14-day windows instead of day over day.
   - "Identical listeners *and* plays" is treated as a missing measurement, not 0% growth, which would have sunk those artists in the rankings.
   - I tracked `stats_last_changed` to measure the refresh rate. It turned out to be roughly daily; the first two snapshots were only 12 hours apart. I verified the assumption instead of over-engineering around it.
2. **The same artist stored twice, with different numbers.** ADÉLA existed under two MusicBrainz IDs, and the two lookups returned different cached values. Mixing them created a fake overnight jump that ranked her #1 in a local test. Silver now keeps exactly one key per name per day, chosen by a fixed rule.
3. **Renamed pages look like breakouts.** JAY-Z restyled his name as "JAŸ-Z", which created a new Last.fm page that ranked near the top. I added a `possible_alias_of` flag: when an artist's name, with accents and punctuation stripped, equals one of their own similar artists' names. It caught 9 cases, including Givēon/Giveon and full-width "３８５", and these are excluded downstream.
4. **Name matching between Ticketmaster and Last.fm.**
   - Matching goes in order: MusicBrainz ID (45% of performers have one), then exact normalized name, then fuzzy matching with `rapidfuzz`.
   - Fuzzy scores of 95+ are auto-matched; 85–95 go to a human review queue.
   - Names under 5 characters must match exactly, and tribute acts are never matched.
   - On real data, all 12 borderline candidates were genuinely *different* artists (horsegiirL vs Horsegirl at 94.7, Spoons vs Spoon), and none was auto-matched.

## 5. LLM scout briefs (`ai_query`)
- **The prompts contain only facts:** growth, charts, tags, bio, similar artists, shows. The model is told not to add anything.
- **The prompt builder is unit-tested; the LLM output isn't.** You can't unit-test a model's writing, but you can test exactly what it's given.
- **Model choice.** I compared three Databricks-hosted models on the same prompts:

  | Model | Result |
  |---|---|
  | Qwen3-Next 80B | Misread "rank #103" as "top 103" |
  | Llama 3.3 70B | Faithful but flat |
  | gpt-oss-120b | Most useful detail; chosen |

- **Product insight: no model surfaced signing status unprompted**, even though Florence Road's bio says they signed to Warner Records. That's the most important fact for A&R. Prompt v2 requires a Signing status section that quotes the bio or says "not confirmed unsigned". It now surfaces things like CHXRRY's XO Records deal.
- **Hardening for production:**
  - `failOnError => false`, so one bad call doesn't fail the whole batch.
  - Empty completions (gpt-oss sometimes returns nothing) get one retry with Llama 3.3.
  - Each artist gets exactly one stored model call. I caught a bug where writing `ai_query` three times in one SELECT could call the model three times.
  - Odd Unicode punctuation is normalized.
  - The exact prompt is stored with every brief for auditing.

## 6. Genie ("Aldous")
The Genie space runs on the gold tables. Its instructions translate business language into filters:
- "rising" means a high breakout score;
- "small" means under 500k listeners;
- "cheap" means $30 or less, keeping shows with no listed price;
- restyled-alias pages are always excluded.

I wrote 4 example SQL queries and tested all of them against real tables before loading them into Genie. The whole configuration is kept in git (`genie/instructions.md`).

## 7. Engineering practice
- **Databricks Asset Bundles:** jobs, schedules and dependencies are defined as code, with separate dev and prod targets. The package is built as a wheel and run with `python_wheel_task`.
- **60 pytest tests** covering scoring, matching, API clients (with mocked HTTP), paging, pool logic, gold row types, quality rules and prompts. **GitHub Actions** runs ruff and pytest on every push.
- **Automated quality checks** run as the last pipeline task, and any failure emails me:
  - freshness;
  - coverage of at least 90% of the pool;
  - no duplicates;
  - bronze→silver rows lost in cleaning under 1%;
  - gold row count matching silver;
  - scores within 0–100.
- **API keys** live in a Databricks secret scope, or a git-ignored `.env` locally.
- **Every data finding is written up** in `docs/data_notes.md`.

## Limitations (say these before you're asked)
- Last.fm's audience skews toward certain demographics and genres, so this is one signal, not the whole picture.
- Ticketmaster misses many of the smallest venues, and only about 12% of matched shows list a price.
- The history is short, so the tool **surfaces fast growth; it doesn't predict hits.** There's no backtest.
- Signing status comes only from user-written bios, so "no label mentioned" doesn't mean unsigned.

## Likely interview questions
- **"Why log growth and a robust z-score?"** Log rates are symmetric and can be compared across artist sizes. Median and MAD resist outliers, like the alias pages.
- **"How do you know the LLM isn't making things up?"** A fact-only prompt, a stored prompt for each brief, and manual checks. For example, I confirmed "born in Toronto" against the bio. The remaining failure mode is light embellishment of tags.
- **"What would you do with more time or data?"** Backtest the score once there are months of history; add YouTube velocity using the YouTube links Ticketmaster already provides (39% of performers); let the review queue feed back into matching.
- **"Why Databricks for 5k rows a day?"** Honest answer: this data would fit in pandas. The point was the production patterns: governance, scheduling, dev/prod separation, Genie over governed tables. The pure-Python core means the heavy parts would scale without a rewrite.
