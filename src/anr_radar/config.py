"""Static configuration: seed countries/tags, pool sizing, and storage layout."""

DEFAULT_LANDING_ROOT = "/Volumes/workspace/anr_bronze/landing"
SECRET_SCOPE = "anr_radar"

# Last.fm expects ISO 3166-1 country names.
SEED_COUNTRIES = [
    "Canada",
    "United States",
    "United Kingdom",
    "Ireland",
    "Australia",
    "Brazil",
    "Mexico",
    "Argentina",
    "Colombia",
    "Chile",
    "Spain",
    "France",
    "Germany",
    "Italy",
    "Netherlands",
    "Sweden",
    "Poland",
    "Nigeria",
    "South Africa",
    "Japan",
    "Korea, Republic of",
    "Philippines",
]

SEED_TAGS = [
    "indie pop",
    "indie rock",
    "bedroom pop",
    "shoegaze",
    "alternative",
    "hip-hop",
    "rap",
    "rnb",
    "soul",
    "electronic",
    "house",
    "techno",
    "hyperpop",
    "folk",
    "singer-songwriter",
    "punk",
    "metal",
    "reggaeton",
    "afrobeats",
    "k-pop",
    "jazz",
]

GEO_LIMIT = 100  # top artists per country (also stored daily for chart-entry signals)
TAG_PAGES = 2  # deeper tag pages reach more mid-tier artists
TAG_PAGE_SIZE = 100
SIMILAR_LIMIT = 15  # neighbours fetched per seed in the 1-hop expansion
MAX_POOL_SIZE = 5000  # cap on the candidate pool (existing members are always kept)
MAX_SEED_SHARE = 0.4  # at most 40% of the pool from (mostly superstar) seed charts
POOL_MAX_AGE_DAYS = 7  # rebuild the pool weekly

LASTFM_REQUESTS_PER_SEC = 4.0

# --- Breakout signals ------------------------------------------------------
# Listener size bands: (lower bound, label). Growth is compared within a band so that
# 20k -> 26k counts for more than 2M -> 2.1M.
SIZE_BANDS = [
    (0, "<10k"),
    (10_000, "10k-50k"),
    (50_000, "50k-500k"),
    (500_000, "500k-2M"),
    (2_000_000, "2M+"),
]
SHORT_WINDOW_DAYS = 7
LONG_WINDOW_DAYS = 14
CHART_WINDOW_DAYS = 14
MIN_BAND_SIZE = 10  # smaller bands fall back to pool-wide statistics
Z_CLIP = 5.0  # soft cap on size-adjusted z-scores

# Breakout score components. Missing components (e.g. no YouTube data yet) are dropped and
# the remaining weights renormalised.
SCORE_WEIGHTS = {
    "listener_growth_z": 0.45,
    "play_growth_z": 0.25,
    "chart_entry_points": 0.15,
    "yt_view_growth_z": 0.15,
}
CHART_ENTRY_CAP = 3  # each new country chart ~ one standard deviation, capped
