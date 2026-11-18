# A&R Radar limitations (read before making claims)

- **It surfaces fast growth; it does not predict hits.** The history is weeks long and the
  score has not been backtested. Never say an artist "will" break.
- **Last.fm audience skew.** Only people who scrobble are counted, and they skew toward
  certain demographics and genres. One signal, not the whole market.
- **Cached stats.** Last.fm refreshes artist totals on its own schedule, so a jump can be a
  stale cache catching up rather than a real one-day spike.
- **Ticketmaster coverage.** Many of the smallest venues sell through Dice, Eventbrite or their
  own sites, so an artist can have shows we don't see. Only ~12% of matched shows list a price,
  and a listed minimum of 0 may be a placeholder rather than a free show: check the ticket link.
  Toronto and Montréal only.
- **Sold out can't be confirmed.** Ticketmaster's public API has no sold-out field (real
  availability is in a partner-only API). "off sale" during the sale window is the closest
  signal. Resale availability isn't covered.
- **Name matching.** Shows are linked to artists by MusicBrainz ID, exact name, or fuzzy name
  (≥ 95 similarity); uncertain matches are held back, so a few real shows are missing.
- **Snapshot.** This server reads a local export, not live data. Check `data_status` before
  saying "today" or "this week".
- **Personal recommendations** can only suggest the ~6k artists the pipeline tracks, and only
  see your artists that we track (check `coverage`). Last.fm "similar" means shared
  listeners, not necessarily the same sound. Your listening is read live and not stored.
  A username unlocks anyone's public listening, so only look up the user's own.
- **Signing status** comes from user-written bios. "No label mentioned" ≠ unsigned.
- **Country.** No home-country field exists; country filters use chart presence or
  nationality tags (see definitions).
