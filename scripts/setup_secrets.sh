#!/usr/bin/env bash
# One-time: store API keys in a Databricks secret scope (reads them from .env).
set -euo pipefail
source .env
databricks secrets create-scope anr_radar 2>/dev/null || true
databricks secrets put-secret anr_radar lastfm_key --string-value "$LASTFM_API_KEY"
[ -n "${TICKETMASTER_API_KEY:-}" ] && databricks secrets put-secret anr_radar ticketmaster_key --string-value "$TICKETMASTER_API_KEY"
[ -n "${YOUTUBE_API_KEY:-}" ] && databricks secrets put-secret anr_radar youtube_key --string-value "$YOUTUBE_API_KEY"
databricks secrets list-secrets anr_radar
