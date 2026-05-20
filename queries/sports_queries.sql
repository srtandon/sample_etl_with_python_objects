-- SQL queries over ingested sports Parquet files.
--
-- Prerequisites:
--   python scripts/run_sports.py --feed fixtures/sports_game.json
--   python scripts/run_sports.py --feed fixtures/sports_game_v2.json
--
-- Then run via:
--   duckdb -c ".read queries/sports_queries.sql"
--
-- Note: if files from different sports have different schemas, add
-- union_by_name=true to read_parquet() to align columns by name.


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Final scores for all games
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    record_id   AS game_id,
    home_team_name,
    home_score,
    away_team_name,
    away_score,
    home_score - away_score AS margin
FROM read_parquet('output/nba_games/*.parquet')
ORDER BY game_id;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Period-by-period breakdown
--    (These columns only exist because JsonFlattener named them consistently.
--     A procedural parser would need separate logic for each period level.)
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    record_id AS game_id,
    periods_1_home_score, periods_1_away_score,
    periods_2_home_score, periods_2_away_score,
    periods_3_home_score, periods_3_away_score,
    periods_4_home_score, periods_4_away_score
FROM read_parquet('output/nba_games/*.parquet');


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Running score per period (UNPIVOT-style with UNION)
-- ─────────────────────────────────────────────────────────────────────────────

WITH periods AS (
    SELECT record_id AS game_id, 1 AS period,
           periods_1_home_score AS home, periods_1_away_score AS away
    FROM read_parquet('output/nba_games/*.parquet')
    UNION ALL
    SELECT record_id, 2, periods_2_home_score, periods_2_away_score
    FROM read_parquet('output/nba_games/*.parquet')
    UNION ALL
    SELECT record_id, 3, periods_3_home_score, periods_3_away_score
    FROM read_parquet('output/nba_games/*.parquet')
    UNION ALL
    SELECT record_id, 4, periods_4_home_score, periods_4_away_score
    FROM read_parquet('output/nba_games/*.parquet')
)
SELECT
    game_id,
    period,
    home                                          AS period_home_score,
    away                                          AS period_away_score,
    SUM(home) OVER (PARTITION BY game_id ORDER BY period) AS cumulative_home,
    SUM(away) OVER (PARTITION BY game_id ORDER BY period) AS cumulative_away
FROM periods
ORDER BY game_id, period;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Ingestion audit: confirms both feed versions resolved to the same data
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    record_id,
    league_name,
    region_name,
    season,
    ingested_at
FROM read_parquet('output/nba_games/*.parquet')
ORDER BY ingested_at;
