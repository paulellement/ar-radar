# Schema used by the MCP server

Source of truth: `src/anr_radar/transform/gold.py` (table builds + Unity Catalog comments),
`src/anr_radar/briefs/generate.py` (briefs), `src/anr_radar/config.py` and
`src/anr_radar/signals/scoring.py` (definitions). Prod tables live in `workspace.anr_gold`;
dev in `workspace.dev_anr_gold`.

All four tables join on **`artist_key`** (`mbid:<MusicBrainz ID>` or `name:<normalized name>`),
not on a bare `mbid` as the original plan assumed.

## Plan vs. reality

| Plan assumed | Actual | Consequence |
|---|---|---|
| join key `mbid` | `artist_key` | all joins use `artist_key` |
| `country` column on signals | none; per-country info is `chart_countries` (Last.fm top-100 per country today) plus nationality tags | `country` filter = in that country's chart, or a nationality tag (`canadian`), labelled per row |
| indie flag | none | "indie" is just a genre tag match |
| silver `artist_daily` for history | gold `artist_growth_history` (same data + daily change) | use the gold table |
| `upcoming_shows` or silver `events` | gold `upcoming_shows` already joined to signals | no join needed for shows |
| scout briefs table | gold `scout_briefs` (rebuilt weekly, latest run only) | one brief per top-50 artist |

## Tables exported to the snapshot

### `artist_signals` (gold) — one row per artist, latest snapshot day (~5,700 rows)

| Column | Type | Meaning |
|---|---|---|
| artist_key | STRING | stable ID |
| name | STRING | Last.fm name |
| as_of_date | DATE | snapshot day the signals are for (the server's "today") |
| genres | STRING | top 3 tags, comma-separated |
| tags | ARRAY<STRING> | Last.fm tags, most-used first |
| lastfm_listeners / lastfm_playcount | BIGINT | all-time totals (artist size) |
| size_band | STRING | `<10k`, `10k-50k`, `50k-500k`, `500k-2M`, `2M+` |
| breakout_score | DOUBLE | 0-100 percentile (null = no fresh data) |
| breakout_rank | INT | 1 = highest score |
| listeners_7d_growth_pct / listeners_14d_growth_pct / plays_7d_growth_pct | DOUBLE | growth, % |
| new_listeners_7d | BIGINT | listeners gained over 7 days |
| new_country_charts_14d | INT | country top-100s entered in 14 days |
| chart_countries | ARRAY<STRING> | country top-100s the artist is in today (Last.fm country names, e.g. `Canada`, `Korea, Republic of`) |
| listener_growth_z / play_growth_z | DOUBLE | growth vs. same-size peers, robust z, soft-capped ±5 |
| is_provisional | BOOLEAN | growth measured over < 7 days |
| history_days, growth_window_days | INT | how much history backs the numbers |
| stats_last_changed | DATE | last day Last.fm refreshed this artist's cached stats |
| on_tour | BOOLEAN | Last.fm on-tour flag |
| lastfm_url | STRING | artist page |
| possible_alias_of | STRING | set = restyled page of an established act (e.g. JAŸ-Z); growth is an artifact, always excluded from rankings |

### `artist_growth_history` (gold) — one row per artist per day; **last 30 snapshot days exported**

`artist_key, name, snapshot_date DATE, lastfm_listeners, lastfm_playcount, listeners_change, playcount_change`
(changes are vs. the previous snapshot; 0 on days Last.fm didn't refresh its cache).

### `upcoming_shows` (gold) — one row per (show, tracked artist on the bill)

`event_date DATE, local_time, city, venue, event_name, event_id, artist_key, artist_name, is_headliner,
breakout_score, breakout_rank, size_band, lastfm_listeners, genres, listeners_7d_growth_pct,
is_provisional, min_price, max_price, currency, ticket_url, tm_genre, status, match_method`

Toronto and Montréal only, next 90 days from the pipeline run, cancelled shows removed, only
trusted performer→artist matches (`mbid`, `exact`, `fuzzy` ≥ 95). Prices are CAD; `min_price` is
null for most shows (~12% list a price).

### `scout_briefs` (gold) — one row per briefed artist (top 50 under 2M listeners, weekly)

`artist_key, name, as_of_date, brief_md, signing_status, label_mentioned, model, prompt_version, generated_at`
(failed generations, where `error IS NOT NULL`, are not exported; the large `prompt` audit column is left out).

### `artist_similar` (silver) — one row per Last.fm "similar artist" edge (~58k)

`from_key, to_key, to_name, match DOUBLE (0-1)`, from `artist.getSimilar` during the latest
candidate-pool build. `from_key` is a tracked artist; `to_key` may or may not be tracked. Used
as the taste graph for the personal tools (both directions).

### `snapshot_meta` (written by the export) — one row per exported table

`table_name, row_count, exported_at TIMESTAMP, source` (`databricks:<catalog>.<prefix>*` or `files:<dir>`).

## Definitions (served as the `ar-radar://definitions` resource)

See `context/definitions.md`. Summary: breakout score = percentile of a weighted mix of
size-adjusted growth z-scores (listeners 45%, plays 25%, new country charts 15%, YouTube 15% when
present, weights renormalised when missing); size bands are by all-time Last.fm listeners.
