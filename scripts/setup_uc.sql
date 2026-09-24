-- One-time Unity Catalog setup. Run in the SQL editor (or: databricks sql ...).
CREATE SCHEMA IF NOT EXISTS workspace.anr_bronze COMMENT 'A&R Radar: raw API snapshots';
CREATE SCHEMA IF NOT EXISTS workspace.anr_silver COMMENT 'A&R Radar: cleaned, deduplicated tables';
CREATE SCHEMA IF NOT EXISTS workspace.anr_gold   COMMENT 'A&R Radar: signals, shows, briefs (Genie + app read from here)';
CREATE VOLUME IF NOT EXISTS workspace.anr_bronze.landing
  COMMENT 'Raw JSONL from the collectors, partitioned as <source>/<dataset>/dt=YYYY-MM-DD/';
