"""One-page scout briefs for the top breakout artists, written by an LLM via ai_query.

The prompt carries every fact the brief may use; the model is told not to add any. Prompt
building is pure Python (tested); generation is one batched SQL ai_query call.
"""

import datetime as dt
import logging

from anr_radar.spark import set_comments
from anr_radar.tables import Tables

log = logging.getLogger(__name__)

PROMPT_VERSION = "v2"
MAX_BIO_CHARS = 900

INSTRUCTIONS = """You are writing a one-page scout brief for an A&R team at a record label.
Use ONLY the facts below. Do not invent anything: no streaming numbers, label deals,
awards, collaborations, or career history that isn't stated. If a fact is missing, say so
or leave it out. Numbers come from Last.fm, which only covers people who log their
listening there, so treat them as one signal, not the whole picture.

Write in Markdown, about 200-250 words, using plain ASCII punctuation (normal hyphens and
spaces), with exactly these sections:
### Who they are
One or two sentences: sound, scene and origin, from the tags and bio only.
### Signing status
If the bio names a record label, publisher or management company, state it plainly and
quote the bio. Otherwise write exactly: "No label mentioned in the Last.fm bio (not
confirmed unsigned)." Never guess.
### What's growing
The growth numbers in plain language, compared with artists of a similar size. The rank
is the artist's position among all tracked artists.
### Where
Country charts they are in or have entered. Say "no chart entries yet" if there are none.
### Sounds like
The similar artists listed.
### Live
Upcoming shows listed below, or "No upcoming Toronto/Montreal shows found."
### Caveats
Always include: short history if provisional, Last.fm audience skew, and that this flags
fast growth rather than predicting success."""


# Some models emit non-breaking spaces and Unicode hyphens; normalise to ASCII.
CLEAN_SQL = (
    r"regexp_replace(regexp_replace({expr}, '[\\x{{2010}}-\\x{{2015}}]', '-'), "
    r"'[\\x{{00A0}}\\x{{202F}}\\x{{2009}}]', ' ')"
)


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def _num(v) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def build_prompt(facts: dict) -> str:
    bio = (facts.get("bio_summary") or "").strip()
    if len(bio) > MAX_BIO_CHARS:
        bio = bio[:MAX_BIO_CHARS].rsplit(" ", 1)[0] + "..."
    shows = facts.get("shows") or []
    show_lines = (
        "\n".join(
            f"- {s['event_date']}: {s['venue']}, {s['city']}"
            + (f" (from {s['min_price']:.0f} {s['currency']})" if s.get("min_price") else "")
            for s in shows
        )
        or "- none found"
    )
    history = (
        f"{facts['growth_window_days']} days (PROVISIONAL: less than 7 days of history)"
        if facts.get("is_provisional")
        else f"{facts.get('growth_window_days')} days"
    )
    return f"""{INSTRUCTIONS}

FACTS
Artist: {facts["name"]}
Last.fm listeners (all time): {_num(facts.get("lastfm_listeners"))} (size band {facts.get("size_band")})
Breakout score: {facts.get("breakout_score")} / 100 (rank #{facts.get("breakout_rank")}); growth relative to artists of similar size
Listener growth, 7 days: {_pct(facts.get("listeners_7d_growth_pct"))} ({_num(facts.get("new_listeners_7d"))} new listeners); measured over {history}
Listener growth, 14 days: {_pct(facts.get("listeners_14d_growth_pct"))}
Play-count growth, 7 days: {_pct(facts.get("plays_7d_growth_pct"))}
In country top-100 charts today: {", ".join(facts.get("chart_countries") or []) or "none"}
New country charts entered in 14 days: {facts.get("new_country_charts_14d") or 0}
Tags: {", ".join(facts.get("tags") or []) or "none"}
Similar artists: {", ".join(facts.get("similar_names") or []) or "none listed"}
Bio (Last.fm, user-written): {bio or "none"}
Upcoming shows:
{show_lines}"""


def load_facts(spark, t: Tables, top_n: int, exclude_bands: tuple[str, ...]) -> list[dict]:
    bands = ", ".join(f"'{b}'" for b in exclude_bands) or "''"
    rows = spark.sql(f"""
        SELECT s.*, p.bio_summary, p.similar_names
        FROM {t("gold", "artist_signals")} s
        LEFT JOIN {t("silver", "artist_profile")} p USING (artist_key)
        WHERE s.breakout_score IS NOT NULL AND s.size_band NOT IN ({bands})
          AND s.possible_alias_of IS NULL
        ORDER BY s.breakout_score DESC
        LIMIT {int(top_n)}
    """).collect()
    facts = {r.artist_key: r.asDict(recursive=True) for r in rows}
    if facts:
        keys = ", ".join(f"'{k.replace(chr(39), chr(39) * 2)}'" for k in facts)
        for s in spark.sql(f"""
            SELECT artist_key, event_date, venue, city, min_price, currency
            FROM {t("gold", "upcoming_shows")} WHERE artist_key IN ({keys})
            ORDER BY event_date""").collect():
            facts[s.artist_key].setdefault("shows", []).append(s.asDict())
    return list(facts.values())


def _call(model: str) -> str:
    """One ai_query call -> struct<result, errorMessage>.

    Reasoning models spend part of max_tokens thinking before they write, hence the budget.
    failOnError => false: one bad call records an error instead of failing the batch."""
    return (
        f"ai_query('{model}', prompt, "
        "modelParameters => named_struct('max_tokens', 3000, 'temperature', 0.3), "
        "failOnError => false)"
    )


def run(
    spark,
    t: Tables,
    model: str,
    top_n: int,
    exclude_bands: tuple[str, ...],
    fallback_model: str | None = None,
) -> int:
    facts = load_facts(spark, t, top_n, exclude_bands)
    prompts = [(f["artist_key"], f["name"], f["as_of_date"], build_prompt(f)) for f in facts]
    spark.createDataFrame(
        prompts, "artist_key STRING, name STRING, as_of_date DATE, prompt STRING"
    ).createOrReplaceTempView("brief_prompts")

    table = t("gold", "scout_briefs")
    raw = t("gold", "scout_briefs_raw")
    # Each model call happens exactly once and is stored; columns are derived afterwards.
    # (Referencing ai_query() several times in one SELECT can call the model several times.)
    spark.sql(f"""
        CREATE OR REPLACE TABLE {raw} AS
        SELECT artist_key, name, as_of_date, prompt, '{model}' AS model,
               {_call(model)} AS response, current_timestamp() AS generated_at
        FROM brief_prompts
    """)
    empty = "coalesce(trim(response.result), '') = ''"
    if fallback_model:  # gpt-oss occasionally returns an empty completion: retry once
        spark.sql(f"""
            MERGE INTO {raw} b
            USING (SELECT artist_key, {_call(fallback_model)} AS response
                   FROM {raw} WHERE {empty}) r
            ON b.artist_key = r.artist_key
            WHEN MATCHED THEN UPDATE SET
              response = r.response, model = '{fallback_model}', generated_at = current_timestamp()
        """)
    spark.sql(f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT artist_key, name, as_of_date,
               CASE WHEN NOT ({empty}) THEN trim({CLEAN_SQL.format(expr="response.result")})
               END AS brief_md,
               CASE WHEN {empty} THEN coalesce(response.errorMessage, 'empty response')
               END AS error,
               model, '{PROMPT_VERSION}' AS prompt_version, generated_at, prompt
        FROM {raw}
    """)
    failed = spark.sql(f"SELECT count(*) FROM {table} WHERE error IS NOT NULL").first()[0]
    if failed:
        log.warning("%d of %d briefs failed; see the error column", failed, len(prompts))
    set_comments(
        spark,
        table,
        "LLM-written one-page scout briefs for the top breakout artists (excluding 2M+ "
        "listeners and likely aliases), refreshed weekly. Generated only from the facts in the "
        "prompt column.",
        {
            "brief_md": "The brief, in Markdown",
            "prompt": "Exact facts and instructions the model was given (for auditing)",
        },
    )
    log.info("wrote %d scout briefs with %s at %s", len(prompts), model, dt.datetime.now(dt.UTC))
    return len(prompts)
