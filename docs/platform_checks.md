# Platform checks (Day 0, 2026-09-23)

Results from `scripts/day0_checks.py` on Databricks Free Edition (serverless).

| Check | Result | Decision |
|---|---|---|
| Egress: ws.audioscrobbler.com | Reachable (HTTP 400 without key) | Collector runs as a Databricks Job |
| Egress: www.googleapis.com | Reachable (HTTP 403 without key) | YouTube collection can run in Databricks |
| Egress: app.ticketmaster.com | Reachable (HTTP 401 without key) | Events collector runs in Databricks |
| Egress: musicbrainz.org | Reachable (HTTP 200) | |
| Egress: api.anthropic.com | Reachable (HTTP 401 without key) | Claude fallback possible from inside Databricks |
| Foundation Model endpoints | Available: gpt-oss-120b/20b, qwen3-next-80b, qwen3.5-122b, llama-4-maverick, llama-3.3-70b, llama-3.1-8b, gemma-3-12b, embeddings (gte-large, bge-large, qwen3-embedding) | |
| `ai_query` | Works (`databricks-meta-llama-3-3-70b-instruct` replied "ok") | Scout briefs via `ai_query` in SQL; Claude API not needed |

Consequence: the GitHub Actions collector (`.github/workflows/collector.yml`) stays as an unused fallback.
Embedding endpoints are also available for the stretch "if you like X" similarity feature.
