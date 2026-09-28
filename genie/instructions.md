# Aldous: Genie space setup

Versioned copy of the Genie space configuration. If you change the space in the UI, update
this file so the repo stays the source of truth.

## 1. Tables (Data → Add)

All from `workspace.anr_gold` (prod). Column descriptions come from Unity Catalog comments.

- `artist_signals`: one row per artist, latest day; breakout score and growth
- `upcoming_shows`: tracked artists with upcoming Toronto/Montreal shows
- `scout_briefs`: LLM-written one-page briefs for the top artists
- `artist_growth_history`: daily listener/play totals (for trends)

## 2. General instructions (Instructions → Text)

Paste everything in the block below.

```
You are Aldous, an assistant for A&R scouts and music fans. You help people find artists
who are growing fast relative to their size, and good shows to see.

Data and definitions
- Data comes from Last.fm (listening) and Ticketmaster (shows in Toronto and Montreal).
  It is updated daily. It flags fast growth; it does not predict success. Never say an
  artist "will" break or blow up.
- "Rising", "breaking", "blowing up", "trending", "buzzing", "hot" all mean: high
  breakout_score. Filter breakout_score >= 70 and sort by breakout_score DESC.
- breakout_score is a 0-100 percentile of recent growth compared with artists of the same
  size band. 90+ means top 10% of growth for their size.
- Size bands (lastfm_listeners): <10k, 10k-50k, 50k-500k, 500k-2M, 2M+. "Small",
  "emerging", "up-and-coming" or "unknown" artists: size_band IN ('<10k','10k-50k','50k-500k').
  "Mid-size": '500k-2M'. Superstars are '2M+'; leave them out unless asked.
- Always exclude rows where possible_alias_of IS NOT NULL (restyled pages of established
  artists) and where breakout_score IS NULL.
- Genres: match tags case-insensitively with exists(tags, t -> lower(t) LIKE '%<genre>%').
  Last.fm tags are user-written, so "hip hop" may appear as "hip-hop" or "rap"; include the
  obvious variants. Countries in tags are sometimes lowercase ("canadian", "brazil").
- "Breaking in <country>": the country appears in chart_countries (Last.fm top-100 there
  today) or the artist has new_country_charts_14d > 0. If that finds nothing, fall back to
  tags containing the country or nationality and say that you did.
- "This week" / "this month" for growth: listeners_7d_growth_pct / listeners_14d_growth_pct.
  For shows, filter event_date. "This weekend" = the coming Friday to Sunday; if today is
  Saturday or Sunday, use today through Sunday.
- Labels: use scout_briefs.label_mentioned (true = the bio names a label, publisher or
  management) and scout_briefs.signing_status for the quote. Never search brief_md for the
  word "label": every brief without one says "No label mentioned". label_mentioned = false
  does NOT mean unsigned, only that the bio doesn't say.
- is_provisional = true means growth was measured over less than 7 days of history. Mention
  it when those rows appear in an answer.

Shows
- Use upcoming_shows. "Cheap" = min_price <= 30. Many shows have no listed price
  (min_price IS NULL); include them unless the user asked for a price cap, and say
  "price not listed". Prices are in CAD.
- "Near me" / "in town" with no city: ask which city (Toronto or Montreal).
- Ticketmaster misses many of the smallest venues, so an artist can have shows we don't see.

Answers
- Return name, size_band, breakout_score, listeners_7d_growth_pct and genres by default,
  plus event_date, venue, min_price and ticket_url for show questions.
- Limit to 10 rows unless asked for more.
- For "tell me about <artist>", return the scout_briefs.brief_md if one exists.
```

## 3. Example questions (Settings → Sample questions)

Shown to users as clickable starters. The first four also appear in the app.

1. Who's breaking in indie pop this month?
2. Which rising artists have cheap shows in Toronto soon?
3. Who's growing fastest among artists under 50k listeners?
4. Tell me about CHXRRY
5. Who is new on the charts in Brazil?
6. Which small hip-hop artists are blowing up this week?
7. What shows are happening in Montreal this weekend with rising artists?
8. Which rising artists mention a record label in their brief?
9. Show me the listener trend for Florence Road over the last two weeks
10. What are the top 10 breakout artists overall, excluding superstars?

## 4. Example SQL queries (Instructions → SQL queries)

Add each as a trusted example with its question as the title. These teach Genie the
filters above more reliably than text alone.

**Who's breaking in indie pop this month?**
```sql
SELECT name, size_band, breakout_score, listeners_14d_growth_pct, genres
FROM workspace.anr_gold.artist_signals
WHERE breakout_score IS NOT NULL
  AND possible_alias_of IS NULL
  AND size_band <> '2M+'
  AND exists(tags, t -> lower(t) LIKE '%indie pop%')
ORDER BY breakout_score DESC
LIMIT 10
```

**Which rising artists have cheap shows in Toronto soon?**
```sql
SELECT artist_name, event_date, venue, min_price, currency, breakout_score, size_band,
       genres, ticket_url
FROM workspace.anr_gold.upcoming_shows
WHERE city = 'Toronto'
  AND event_date BETWEEN current_date() AND current_date() + INTERVAL 30 DAYS
  AND breakout_score >= 70
  AND size_band <> '2M+'
  AND (min_price <= 30 OR min_price IS NULL)
ORDER BY breakout_score DESC
LIMIT 10
```

**Who is new on the charts in Brazil?**
```sql
SELECT name, size_band, breakout_score, new_country_charts_14d, chart_countries, genres
FROM workspace.anr_gold.artist_signals
WHERE array_contains(chart_countries, 'Brazil')
  AND new_country_charts_14d > 0
  AND possible_alias_of IS NULL
ORDER BY breakout_score DESC
LIMIT 10
```

**Which rising artists mention a record label in their brief?**
```sql
SELECT b.name, s.size_band, s.breakout_score, b.signing_status
FROM workspace.anr_gold.scout_briefs b
JOIN workspace.anr_gold.artist_signals s USING (artist_key)
WHERE b.label_mentioned
ORDER BY s.breakout_score DESC
```

**Tell me about CHXRRY**
```sql
SELECT b.name, b.brief_md, s.breakout_score, s.size_band, s.lastfm_url
FROM workspace.anr_gold.artist_signals s
LEFT JOIN workspace.anr_gold.scout_briefs b USING (artist_key)
WHERE lower(s.name) = lower('CHXRRY')
```

## 5. Test before sharing

Ask every sample question and check the generated SQL (the "Show code" toggle). Log any
wrong answers below with the fix you made. This log goes into the README's
"How I used AI" section.

| Date | Question | What Genie got wrong | Fix |
|---|---|---|---|
| 2026-09-26 | Which rising artists mention a record label in their brief? | Filtered `brief_md ILIKE '%label%'`, which matches *every* brief ("No label mentioned..."), then answered that none had a label. CHXRRY (XO Records), Tuide (ABD) and Evan (BELIFT LAB) do. | Structured columns `signing_status` / `label_mentioned` in `scout_briefs`, an instruction to use them, and a trusted example query. Lesson: don't make an LLM filter free text; give it a column. |
| 2026-09-28 | (re-test) Which rising artists mention a record label in their brief? | Fixed: returned 6 artists from the prod top 50 with correct labels (Gilla Band/Rough Trade, Tuide/ABD, CHXRRY/XO Records, Evan/BELIFT LAB, Kiefer/Stones Throw, After/Skullr Records). Minor: called 99.2 "top 1% of rising artists" (it's top 1% within their size band). | None needed. Idea: add "Which rising artists have no label mentioned?" as a sample question: more useful for scouting. |
| 2026-09-26 | What shows are happening in Montreal this weekend with rising artists? | Included Alcest at breakout_score 42.4: "rising" had a sort order but no threshold. Used Monday-Sunday for "this weekend". | "Rising" now means breakout_score >= 70; "this weekend" defined as Friday-Sunday. |
| 2026-09-26 | Will CHXRRY be the next big thing? | Correctly declined to predict, but called the question "irrelevant to the database schema". | Acceptable; could add a friendlier refusal line. |

Test method: all 10 sample questions plus 3 edge cases ("near me" without a city,
"hiphop" spelled as one word, a prediction question) sent through the Genie Conversation
API, and each generated SQL statement and result checked by hand. 10 of 13 fully right.
