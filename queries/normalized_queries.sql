-- SQL queries over the normalized sports.db schema.
--
-- Run:
--   python scripts/run_normalized.py --feed fixtures/football_game_boxscore.json --sport nfl
--   python scripts/run_normalized.py --feed fixtures/schedule.json --sport nhl
--   duckdb output/sports.db ".read queries/normalized_queries.sql"
--
-- Compare the storage cost vs the flat approach:
--   - games table:          14 columns, 1 row per game
--   - team_game_stats:       9 columns, 2 rows per game
--   - period_scores:         4 columns, N rows per game
--   - scoring_plays:         8 columns, N rows per game
--   vs flat Parquet:       300+ columns, 1 row per game (most NULL)


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Final scores — the query that drives any standings view
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    g.sport,
    g.title,
    g.status,
    g.scheduled,
    home.team_name  AS home_team,
    home.score      AS home_score,
    away.team_name  AS away_team,
    away.score      AS away_score,
    v.city          AS venue_city
FROM games g
LEFT JOIN team_game_stats  home ON g.game_id = home.game_id AND home.side = 'home'
LEFT JOIN team_game_stats  away ON g.game_id = away.game_id AND away.side = 'away'
LEFT JOIN venues           v    ON g.venue_id = v.venue_id
ORDER BY g.scheduled;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Period-by-period scoring (works for any number of periods/quarters)
--    No schema change needed when overtime periods appear.
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    g.title,
    p.period,
    MAX(CASE WHEN p.side = 'home' THEN p.points END) AS home_pts,
    MAX(CASE WHEN p.side = 'away' THEN p.points END) AS away_pts
FROM period_scores p
JOIN games g ON p.game_id = g.game_id
GROUP BY g.game_id, g.title, p.period
ORDER BY g.title, p.period;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Cumulative score after each scoring play (running total)
--    Shows how the lead changed throughout the game.
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    g.title,
    sp.quarter,
    sp.clock,
    sp.scoring_team,
    sp.play_type,
    sp.home_points,
    sp.away_points,
    sp.home_points - sp.away_points AS margin
FROM scoring_plays sp
JOIN games g ON sp.game_id = g.game_id
ORDER BY g.title, sp.play_seq;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Scoring play breakdown by type
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    play_type,
    COUNT(*)                                       AS total_plays,
    SUM(away_points - LAG(away_points, 1, 0) OVER (
        PARTITION BY game_id ORDER BY play_seq
    ))                                             AS away_pts_from_type,
    SUM(home_points - LAG(home_points, 1, 0) OVER (
        PARTITION BY game_id ORDER BY play_seq
    ))                                             AS home_pts_from_type
FROM scoring_plays
GROUP BY play_type
ORDER BY total_plays DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Win probability for scheduled games (NHL schedule)
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    g.title,
    g.scheduled,
    home.team_name  AS home_team,
    away.team_name  AS away_team,
    home.wins       AS home_wins,
    home.losses     AS home_losses,
    away.wins       AS away_wins,
    away.losses     AS away_losses
FROM games g
JOIN team_game_stats home ON g.game_id = home.game_id AND home.side = 'home'
JOIN team_game_stats away ON g.game_id = away.game_id AND away.side = 'away'
WHERE g.status = 'scheduled'
ORDER BY g.scheduled;
