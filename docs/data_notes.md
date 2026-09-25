# Data notes

Things learned about the sources that shaped the design.

## Last.fm artist stats are cached, and refresh by artist size (found 2026-09-24)

Comparing the first two snapshots (taken ~12h apart), 96% of artists had *identical*
listeners and playcount. The share that changed fell steeply with size:

| Size band | Changed |
|---|---|
| 2M+ | 20% |
| 500k–2M | 5.7% |
| 50k–500k | 0.4% |
| <50k | ~0% |

When stats do change, the jumps look real (e.g. +402 listeners / +27k plays), so Last.fm
seems to refresh cached `artist.getInfo` stats on a schedule that favours bigger artists.

What we changed:
- Growth is measured over 7- and 14-day windows, never day over day.
- Identical listeners *and* playcount across a window means "cache not refreshed", so it's
  treated as a missing measurement rather than 0% growth (which would wrongly sink those
  artists in the ranking).
- `stats_last_changed` is recorded per artist so the refresh cadence can be measured.

**Update 2026-09-25:** between day 2 and day 3 (a full 24h apart), 99.7% of artists changed.
So the cache refreshes roughly daily, and day 1 -> day 2 looked stale mostly because the
first (manual) run was only ~12h before the first scheduled one. The "unchanged = missing"
rule stays: it still covers artists that miss a refresh. Still worth re-checking the
refresh distribution per size band after a week.

## The same artist can appear under two keys, with different cached numbers

The pool holds some artists twice (e.g. ADÉLA under two different MusicBrainz IDs). On
2026-09-23 the two lookups of the *same* Last.fm page returned 655,378 and 658,963 listeners.
Mixing the two keys produced a fake "+0.5% overnight" that ranked ADÉLA #1 in an early
local test. Silver keeps exactly one key per (name, day), deterministically (mbid keys first,
then alphabetical), so each artist's history always comes from the same lookup. New pool
additions are also deduplicated by name.

## Ticketmaster: good small-room coverage, sparse prices, and most performers aren't charting

First pull (2026-09-25): 706 upcoming music events in Toronto and 284 in Montréal over 90
days, including small rooms (Lee's Palace, The Garrison, Sneaky Dee's, The Drake Hotel).

- **Only 18% of Toronto performers were in the chart-seeded pool**, so performers of upcoming
  shows are now added to the pool daily (their own cap: `MAX_EVENT_ARTISTS`). They get growth
  scores after two snapshots.
- **45% of performers carry a MusicBrainz ID** in `externalLinks`, so many matches are exact
  by ID. 39% have a YouTube link, useful later for YouTube stats without `search.list`.
- **Prices are sparse:** only ~12% of matched upcoming shows list a price range. The app must
  treat "no listed price" as unknown, not free.
- **Name matching:** every one of the 12 fuzzy "review" candidates on day one was a
  *different* artist (horsegiirL vs Horsegirl at 94.7, Spoons vs Spoon, MARO vs Mario), and
  none was auto-matched. Hence the high auto threshold (95), exact-only matching for names
  under 5 characters, and never auto-matching tribute acts.
