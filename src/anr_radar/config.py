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
