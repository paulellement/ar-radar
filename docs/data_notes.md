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

Open question: how often do small artists refresh? Check after ~7 days of snapshots
(distribution of days between changes per size band). If some artists refresh less than
weekly, widen the default window for them or rank on the 14-day window.

## The same artist can appear under two keys, with different cached numbers

The pool holds some artists twice (e.g. ADÉLA under two different MusicBrainz IDs). On
2026-09-23 the two lookups of the *same* Last.fm page returned 655,378 and 658,963 listeners.
Mixing the two keys produced a fake "+0.5% overnight" that ranked ADÉLA #1 in an early
local test. Silver keeps exactly one key per (name, day), deterministically (mbid keys first,
then alphabetical), so each artist's history always comes from the same lookup. New pool
additions are also deduplicated by name.
