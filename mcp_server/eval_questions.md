# Mini eval: 5 scouting questions

**Method.** Expected answers were derived by running SQL directly against the snapshot (not
through the server code), and the pass criteria were written down *before* asking Claude.
Each question then went to Claude Code in a fresh session, with only the `ar-radar` MCP server
available (no shell or file tools, so it couldn't bypass the server and read the database).

**Snapshot:** `workspace.anr_gold`, data as of **2026-10-18**, exported 2026-11-17 (30 days
old: the daily job stopped running after Oct 18). Eval run on 2026-11-17.

---

### Q1. "Top 5 breakout artists in Canada right now?"

Expected (2M+ excluded, aliases and unscored excluded, chart OR nationality tag):

| # | Artist | Score | Evidence |
|---|---|---|---|
| 1 | Slayyyter | 100.0 | in Canada's Last.fm top-100 |
| 2 | ADÉLA | 99.9 | chart |
| 3 | Tinashe | 99.9 | chart |
| 4 | CHXRRY | 99.6 | "Canadian" tag |
| 5 | Smush | 99.4 | "canada" tag |

Pass if: these 5 (order of the 99.9 tie doesn't matter), and the answer distinguishes "charting
in Canada" from "Canadian" artists, and mentions the data is from Oct 18 / not current.

### Q2. "Who's rising in indie pop (any country)?"

Expected top 10 (excluding 2M+): Emperor X 99.8, s0rrow 99.7, Dev Lemons 99.6, Tiffany Day 99.6,
Broken Toy Airplanes 99.4, Florence Road 99.3, F3miii 99.3, underscores 99.3, Golden Cats 99.1,
Wakelee 98.9. All have 7-day listener growth of 0.7-2.7%.

Pass if: the top 5 named match (ties may reorder) and no 2M+ artist is presented as "rising"
without saying so.

### Q3. "Tell me about CHXRRY — is their growth real or a one-day spike?"

Expected: 116,208 listeners (50k-500k band), breakout score 99.6. Over 25 daily snapshots
(Sep 23 – Oct 18) listeners rose 108,534 → 116,208 (+7,674); 24 of 24 day-over-day changes were
increases; the largest single day (+580, Oct 15) is 7.6% of the total. **Steady, not a spike.**
Scout brief: bio links the "22" in her former name to signing with XO Records in 2022 (past
affiliation, current label not confirmed). No upcoming Toronto/Montréal shows.

Pass if: says steady/sustained with supporting numbers, mentions the XO Records note with the
caveat, and doesn't invent shows.

### Q4. "Rising artists with shows in Toronto under $30 in the next 30 days?"

Window 2026-11-17 → 2026-12-17, breakout ≥ 70: 60 shows match, but only 7 list a price ≤ $30:
Basht. (Lee's Palace, Nov 22, $28.41, score 99.9), Angela Autumn (Monarch Tavern, Dec 8,
$27.75), Dumb Crush (Sneaky Dee's, Nov 28, $27.75), Fine Food Market (Drake Underground,
Nov 26, $29.57), plus three listed at $0 (Iluka, The Kilans, Thee Sinseers). The rest list no
price; top unpriced by score: daine (The Garrison, Dec 2), Josie Edwards (The Drake Hotel,
Dec 2), Tommy Fleece (Hard Luck Bar, Nov 21).

Pass if: Basht. at Lee's Palace is named, at least 3 of the 4 confirmed paid ≤ $30 shows
appear, unpriced shows are labelled as such (not "free" and not "under $30"), and no show
before Nov 17 is listed.

### Q5. "How fresh is this data?"

Expected: data as of 2026-10-18, exported 2026-11-17, 30 days old.

Pass if: gives the Oct 18 date and says it's about a month old.

---

## Results (2026-11-17, Claude Code headless, Opus 5.5, subscription auth, $0)

**5/5 pass** after one fix. Every claim was checked against the tool output or the scout
brief; no invented facts found.

| Q | Tools called | Result | Notes |
|---|---|---|---|
| 1 | top_breakout_artists(country=Canada) → data_status | ✅ | Exact top 5. Explained that the top 3 *chart* in Canada but aren't Canadian, and that CHXRRY/Smush match via a user tag. Flagged the data as 30 days old. |
| 2 | top_breakout_artists(genre=indie pop) → data_status → 4× artist_profile | ✅ | Top 10 exact. Unprompted, it checked the top artists' trends (all steady) and noted a few match only on a secondary indie pop tag. |
| 3 | artist_profile(CHXRRY) → data_status | ✅ | Steady: +7,674, biggest day 7.6% of the gain. It also noticed that Oct 14 is missing from the history, so the "biggest day" spans two days, and compared the Oct 12 brief with Oct 18 to show growth easing. XO Records caveat kept. |
| 4 | upcoming_shows → data_status → upcoming_shows(require_price) → artist_profile(Basht.) | ✅ | Listed all 7 priced ≤ $30 shows and flagged the $0 listings as possible placeholders. Said the 53 unpriced matches "may still be under $30" and named the top ones. Caught that Basht.'s 99.9 is provisional (2 days of history). |
| 5 | data_status | ✅ (2nd run) | See below. |

**What the eval caught.** Q5's first run gave the right date but also told the user that show
searches start from the snapshot date and would return past gigs. That was true of an earlier
version, and the `days_ahead` docstring still described it. The model was faithfully repeating
my stale docstring. After the docstring was fixed, the rerun was correct. Building the eval
also surfaced three bugs before Claude ever saw them: Montréal is spelled two ways in the
data, timezone-aware timestamps from Databricks broke reads, and cheap priced shows were
crowded out by unpriced ones (fixed with `total_matches`/`priced_matches` and `require_price`,
which Claude then used unprompted in Q4).

**Reproduce:**
```bash
claude -p "<question>" --mcp-config .mcp.json --strict-mcp-config --tools "" \
  --allowedTools "mcp__ar-radar" --output-format stream-json --verbose
```
(`--tools ""` removes the shell and file tools, so answers can only come through the server.)

---

# Personal eval: 3 questions (Last.fm username, 2026-11-18)

**Setup.** Paul's own Last.fm account, set as `LASTFM_USERNAME` in `.env` (the questions
don't mention it, so this also tests the configured default). The snapshot is as of
**2026-11-18**, the first run after the pipeline's month-long gap, so "7-day" growth is
measured over 31 days. Expected answers come from `personal.py` on the live Last.fm response
and were cross-checked with SQL (mapping, show list). They were recorded before asking Claude.

### P1. "What's my music taste, and are any of my artists breaking?"

Expected: top artists Kanye West, The Weeknd, Fleetwood Mac, Bon Iver, Mac Miller. Top tags
hip-hop, rap, pop, rnb, soul. 76% of the top 50 are tracked (Bon Iver, Elton John, The
Lumineers and Vince Guaraldi aren't in the pool; checked by SQL). Emerging share is about 2.5%,
so the taste is overwhelmingly established acts. **Breaking (excluding 2M+):** Omar Apollo
86.4, Anderson .Paak 83.0, Car Seat Headrest 71.2, all in the 500k-2M band. 12 of the 2M+
artists also score ≥ 70 but aren't "being early".

Pass if: the 3 breaking artists are named, no 2M+ artist is presented as an early find,
mainstream taste is noted, and the 31-day growth caveat is mentioned.

### P2. "Recommend some rising artists I don't listen to yet."

Expected top 5 (affinity × breakout): Joey Valence & Brae (similar to Denzel Curry,
BROCKHAMPTON), Ty Dolla $ign (Kanye West, Travi$ Scott), IDK (Denzel Curry, Vince Staples),
Bryson Tiller (The Weeknd, Drake), ¥$ (Kanye West, Travi$ Scott). None are in Paul's top
artists.

Pass if: the top 5 match with their `because` reasons, nothing already in the user's top
artists is recommended, and no reason is invented.

### P3. "Any shows for me in Toronto this month?"

*(Answer key corrected after the run: "this month" = the rest of the calendar month, so only
Victoria Monét. The original key below assumed the next 30 days.)*

Expected: exactly 2. **Victoria Monét**, Coca-Cola Coliseum, Nov 21 (similar to Omar Apollo).
**Montell Fish**, The Phoenix Concert Theatre, Dec 9 (similar to Frank Ocean). Neither lists a
price. None of Paul's own artists have Toronto/Montréal shows in the next 90 days.

Pass if: those 2 shows with dates and venues, the reasons given, "price not listed" (not
free), and no invented shows.

## Personal results (2026-11-18, Claude Code headless, Opus 5.5, subscription auth, $0)

**First run: 2/3. After one fix: 3/3.** (P3 was regraded after the run; see its row.)
Every quoted number was checked against the raw tool output in the transcript.

| Q | Tools called | Result | Notes |
|---|---|---|---|
| P1 | my_taste_profile → data_status | ✅ | Named Omar Apollo, Anderson .Paak and Car Seat Headrest as breaking, and explained why the 12 superstars were left out. Said the taste is mostly established (2.5% emerging) and gave the 31-day caveat. Its top-10 order differs from the expected answer because **live listening changed between runs** (Vince Guaraldi jumped to #1, presumably seasonal); it reported the tool data faithfully. |
| P2 | discover_for_me → data_status | ❌ → ✅ | Top 10 and every `because` were correct, but it reported `listeners_7d_growth_pct: 0.212` as **"+21%"**, a 100× overstatement. (It read the same field correctly in P1, so the field was ambiguous.) Fix: stated the units in the server instructions, the tool description and the definitions. **Two reruns were both correct** ("+0.21%", and one explained "0.286 means +0.29%, not +29%"). |
| P3 | data_status → shows_for_me(Toronto, days_ahead=13) | ✅ (regraded) | It read "this month" as the rest of November, so it returned Victoria Monét (Nov 21) and stated the window, the reason, and "price not listed". **Regraded after the run:** the pre-written expected answer assumed "next 30 days" (and so also expected Montell Fish, Dec 9). Paul, who wrote the question, confirmed "this month" means the rest of the calendar month ("next month" would be the next 30 days). The answer was right; the answer key was wrong. No instruction was added, since the model already reads the phrase correctly and a rule per time phrase would be overfitting. |

**What building and running this caught (all fixed, with tests):**
1. `shows_for_me` returned **no shows** for Toronto. It only checked the top 100 of 421
   taste-linked artists, and the real matches ranked below that. The cutoff was removed.
2. Snapshot similar-artist edges pointing to **untracked artists crashed** `shows_for_me`
   (KeyError). The fixture had no such edge, so the tests missed it; one was added.
3. "Your artists breaking" listed **2M+ superstars** (Kanye-sized acts growing 0.2%). They're
   now excluded, like in the rankings, and counted in a note.
4. The P2 **percent-units** misreading (above).

**Open data issue (pipeline, not MCP):** Givēon (#6 in P2, score 93.9) has no
`possible_alias_of` flag. The alias detector needs the plain "Giveon" page to be in the pool
to compare against, and it isn't. The technical summary lists Givēon/Giveon as a known alias
pair, so this score may be an artifact of a restyled page.

**Note (later the same day).** After this eval, "artists you listen to" was widened from the top
50 to the user's all-time top 500 (Paul's point: a show by an artist you play is news even if
they're not a favourite). On the same data, `your_artists_breaking` now lists 20 artists,
including Angela Autumn (10k-50k), and Toronto shows this month go from 2 to 7. The results
above were recorded on the top-50 version and haven't been re-run.
