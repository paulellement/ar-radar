# A&R Radar definitions

**Breakout score (0-100).** How unusual an artist's recent growth is *compared with artists of
the same size*. For each artist we measure growth as a per-day log rate over 7 days (listeners,
plays), turn each into a robust z-score within the artist's size band (median and MAD, so
outliers don't distort it), soft-cap it at ±5, and take a weighted mix: listener growth 45%,
play growth 25%, new country-chart entries 15%, YouTube views 15% (not collected yet, so its
weight is spread over the others). The mix is turned into a percentile: 90+ means top 10% of
growth for their size. Null means no fresh measurement (Last.fm hadn't refreshed its cached
stats), not zero growth. Why size-adjusted: 20k → 26k listeners is a bigger deal than
2M → 2.1M.

**Size bands** (all-time Last.fm listeners): `<10k`, `10k-50k`, `50k-500k`, `500k-2M`, `2M+`.
"Emerging" usually means the first three. `2M+` are superstars and are left out of rankings
unless explicitly requested.

**Growth windows.** `listeners_7d_growth_pct` and `listeners_14d_growth_pct` are percentage
growth over 7 and 14 days, already in percent: `0.21` means +0.21%, not +21%. For artists with
hundreds of thousands of listeners, well under 1% a week is normal; the breakout score says
whether it's unusual for their size. `is_provisional = true` means the artist has less than 7 days of
history, so the 7-day number is extrapolated from a shorter window.

**Breaking in a country.** There is no "home country" column. An artist counts for a country
if they are in that country's Last.fm top-100 today (`country_evidence: chart`) or carry a
nationality tag such as "canadian" (`country_evidence: tag`). Tags are user-written, so the
tag route is weaker evidence.

**Indie.** There is no indie flag; "indie" and "indie pop" are Last.fm tags like any genre.

**Trend pattern** (in `artist_profile`, over the last 30 days of daily snapshots):
- `steady`: listeners rose on 3+ days and no single day is more than half the total gain.
- `concentrated`: one day accounts for more than half the gain (a spike, or Last.fm refreshing
  a stale cache in one jump; check `stats_last_changed`).
- `flat`: no net gain.
- `insufficient_history`: fewer than 3 daily points.
- Gaps between snapshots are listed in `gaps`; a change across a gap is spread over its days.

**Possible alias.** `possible_alias_of` marks a restyled page of an established act (e.g.
JAY-Z as JAŸ-Z). Its "growth" is a new page filling up, so these are always excluded.

**Shows.** Toronto and Montréal only, from Ticketmaster. Prices are CAD; `price_known: false`
means Ticketmaster lists no price, not that the show is free. `role` is `headliner` or
`support`, with the `headliner` and `lineup`. An opening slot is mainly the headliner's show,
so say so; `shows_for_me` ranks it ×0.8.

**Ticket status** is checked live with Ticketmaster for every show returned:
- on sale;
- not on sale yet;
- **off sale**: not selling during its sale window. Often sold out, but Ticketmaster doesn't
  confirm it, so never call it "sold out";
- sales closed;
- rescheduled / postponed, with the current date.

Canceled shows are removed.

**Personal tools** (`my_taste_profile`, `discover_for_me`, `shows_for_me`) read the user's
public Last.fm top artists (all-time + last 3 months). Their artists are matched to ours by
MusicBrainz ID or exact name only; `coverage` is the share of their top 50 we track.
**Affinity (0-1)** = 0.7 × similar-artist score (sum of "your artist's weight × Last.fm
similarity" over your top artists, scaled so the best match is 1) + 0.3 × tag overlap
(cosine of your weighted tags vs. the artist's top 5). With no similarity links it falls back to
tags alone. `because` names the artists and tags that produced the match. Discovery ranks by
affinity × breakout score / 100 × novelty (see below).
**Your library** = the tracked artists in your all-time top 500 (plus your recent top 50).
Taste weights come from your top 50; your library decides "artists you listen to": they come
first in `shows_for_me`, are never "discovered", and **your artists breaking** = library
artists under 2M listeners with breakout ≥ 70.

**Familiarity** is a dial, not a category: your plays on a log scale against your most-played
artist (1 play ≈ 0.1, a favourite = 1). Nothing is split into "yours" vs "new". An artist you
played once in passing counts as almost new.

**How `shows_for_me` ranks.** Candidates are shows by artists you've played (any amount) or
that Last.fm links to your artists as similar. Artists that only share a tag aren't candidates.
Each show gets a `rank_score` from 0-1 factors, shown in `score_parts`:
- **familiarity**, and **novelty** = 1 − familiarity;
- **match** (taste affinity) and **connection** = max(familiarity, match);
- **breakout** (score / 100);
- **emerging** (1 if under 500k listeners: small rooms, cheaper tickets);
- **cheap** (1 − price / $100; only a listed, non-zero price counts).

`who` shifts the weights; it never filters:

| who | weights |
|---|---|
| any (default) | connection .4, breakout .4, emerging .2 |
| mine | familiarity .55, breakout .25, emerging .2 |
| new | match .35, novelty .25, breakout .25, emerging .15 |

`prefer_cheap` scales those by 0.65 and adds cheap × 0.35. **Discovery** uses the same
dial: rank = affinity × breakout × (1 − familiarity)², so barely-played artists can still be
suggested and favourites can't.

**Signing status.** From the scout brief: `label_mentioned = true` means the artist's Last.fm
bio names a label, publisher or management. `false` does NOT mean unsigned.
