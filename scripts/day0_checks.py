# Databricks notebook source
# MAGIC %md
# MAGIC # Day-0 platform checks
# MAGIC 1. Can Free Edition serverless reach the APIs? (decides: Databricks Job vs GitHub Actions collector)
# MAGIC 2. Are Foundation Model endpoints / `ai_query` available? (decides: Databricks vs Claude API for briefs)
# MAGIC
# MAGIC Record the results in `docs/platform_checks.md`.

# COMMAND ----------

import requests

HOSTS = {
    "lastfm": "https://ws.audioscrobbler.com/2.0/?method=chart.gettopartists&format=json",
    "youtube": "https://www.googleapis.com/youtube/v3/channels",
    "ticketmaster": "https://app.ticketmaster.com/discovery/v2/events.json",
    "musicbrainz": "https://musicbrainz.org/ws/2/artist?query=radiohead&fmt=json&limit=1",
    "anthropic": "https://api.anthropic.com/v1/models",
}
for name, url in HOSTS.items():
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "anr-radar-check/0.1"})
        # Any HTTP status (even 401/403 from missing keys) means the host is reachable.
        print(f"{name:13s} REACHABLE  HTTP {r.status_code}")
    except Exception as e:
        print(f"{name:13s} BLOCKED    {type(e).__name__}: {e}")

# COMMAND ----------

# Real key test for Last.fm (after scripts/setup_secrets.sh)
key = dbutils.secrets.get("anr_radar", "lastfm_key")  # noqa: F821
r = requests.get(
    "https://ws.audioscrobbler.com/2.0/",
    params={"method": "artist.getinfo", "artist": "Radiohead", "api_key": key, "format": "json"},
    timeout=10,
)
print(r.json()["artist"]["stats"])

# COMMAND ----------

from databricks.sdk import WorkspaceClient

endpoints = [e.name for e in WorkspaceClient().serving_endpoints.list()]
print("\n".join(endpoints) or "no serving endpoints")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Swap in a chat endpoint name from the list above if this one isn't present.
# MAGIC SELECT ai_query('databricks-meta-llama-3-3-70b-instruct', 'Reply with the single word: ok') AS reply
