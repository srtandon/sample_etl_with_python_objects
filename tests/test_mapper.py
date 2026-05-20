"""
Tests for GameNormalizer and NormalizedDuckDBSink.

Exercises three things:
  1. Correct row counts from real fixtures (football boxscore, NHL schedule).
  2. Schema integrity: expected columns present in every row.
  3. Sink idempotency: ingesting the same game twice doesn't duplicate rows.
"""

import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FOOTBALL_FIXTURE = ROOT / "fixtures" / "football_game_boxscore_01.json"
SCHEDULE_FIXTURE = ROOT / "fixtures" / "schedule.json"


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def football_payload():
    with FOOTBALL_FIXTURE.open() as f:
        return json.load(f)


@pytest.fixture
def schedule_payload():
    with SCHEDULE_FIXTURE.open() as f:
        return json.load(f)


@pytest.fixture
def schedule_games(schedule_payload):
    """Individual game dicts from the schedule with league/season propagated."""
    from scripts.run_normalized import games_from_payload
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    return games_from_payload(schedule_payload)


# ──────────────────────────────────────────────────────────────────────────────
# GameNormalizer — football boxscore
# ──────────────────────────────────────────────────────────────────────────────


class TestGameNormalizerFootball:
    def test_produces_one_game_row(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer(sport="nfl").normalize(football_payload)
        assert isinstance(ng.game, dict)
        assert ng.game["sport"] == "nfl"

    def test_game_row_has_expected_fields(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        required = {"game_id", "sr_id", "status", "title", "scheduled", "ingested_at"}
        assert required.issubset(ng.game.keys())

    def test_two_team_rows_home_and_away(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        assert len(ng.teams) == 2
        sides = {t["side"] for t in ng.teams}
        assert sides == {"home", "away"}

    def test_team_rows_have_scores(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        home = next(t for t in ng.teams if t["side"] == "home")
        away = next(t for t in ng.teams if t["side"] == "away")
        assert home["score"] == 13
        assert away["score"] == 29

    def test_period_scores_are_extracted(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        # 4 quarters × 2 sides = 8 rows
        assert len(ng.period_scores) == 8

    def test_period_score_row_shape(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        row = ng.period_scores[0]
        assert {"game_id", "side", "period", "points"}.issubset(row.keys())

    def test_home_period_scores_sum_to_final_score(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        home_total = sum(r["points"] for r in ng.period_scores if r["side"] == "home")
        home_final = next(t["score"] for t in ng.teams if t["side"] == "home")
        assert home_total == home_final

    def test_scoring_plays_are_extracted(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        # The fixture has scoring plays (failed conversions are excluded)
        assert len(ng.scoring_plays) > 0

    def test_scoring_play_row_shape(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        play = ng.scoring_plays[0]
        required = {"game_id", "play_seq", "quarter", "clock", "scoring_team", "play_type"}
        assert required.issubset(play.keys())

    def test_venue_row_is_present(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        assert ng.venue is not None
        assert ng.venue["name"] == "Caesars Superdome"
        assert ng.venue["city"] == "New Orleans"

    def test_game_id_property(self, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        assert ng.game_id == football_payload["id"]


# ──────────────────────────────────────────────────────────────────────────────
# GameNormalizer — NHL schedule (future games, no scoring data)
# ──────────────────────────────────────────────────────────────────────────────


class TestGameNormalizerSchedule:
    def test_games_from_payload_unpacks_array(self, schedule_payload):
        from scripts.run_normalized import games_from_payload
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        games = games_from_payload(schedule_payload)
        assert len(games) == len(schedule_payload["games"])

    def test_league_propagated_to_each_game(self, schedule_games):
        for g in schedule_games:
            assert "league" in g, "league should be propagated to each game"

    def test_two_teams_per_game(self, schedule_games):
        from ingestion.mapper import GameNormalizer
        normalizer = GameNormalizer(sport="nhl")
        for game in schedule_games:
            ng = normalizer.normalize(game)
            assert len(ng.teams) == 2, f"expected 2 teams in {game['id']}"

    def test_scheduled_games_have_no_period_scores(self, schedule_games):
        from ingestion.mapper import GameNormalizer
        normalizer = GameNormalizer(sport="nhl")
        scheduled = [g for g in schedule_games if g.get("status") == "scheduled"]
        assert len(scheduled) > 0, "fixture should have at least one scheduled game"
        for game in scheduled:
            ng = normalizer.normalize(game)
            assert ng.period_scores == [], "no period scores for future games"

    def test_league_name_in_game_row(self, schedule_games):
        from ingestion.mapper import GameNormalizer
        normalizer = GameNormalizer(sport="nhl")
        ng = normalizer.normalize(schedule_games[0])
        assert ng.game["league_name"] == "NHL"

    def test_season_year_in_game_row(self, schedule_games):
        from ingestion.mapper import GameNormalizer
        normalizer = GameNormalizer(sport="nhl")
        ng = normalizer.normalize(schedule_games[0])
        assert ng.game["season_year"] == 2025


# ──────────────────────────────────────────────────────────────────────────────
# NormalizedDuckDBSink — schema creation and idempotency
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    from ingestion.sinks.normalized_sink import NormalizedDuckDBSink
    sink = NormalizedDuckDBSink(db_path=tmp_path / "sports.db")
    sink.setup()
    return sink


class TestNormalizedDuckDBSink:
    def test_setup_creates_db_file(self, tmp_path):
        from ingestion.sinks.normalized_sink import NormalizedDuckDBSink
        sink = NormalizedDuckDBSink(db_path=tmp_path / "test.db")
        sink.setup()
        assert (tmp_path / "test.db").exists()

    def test_write_football_game(self, tmp_db, football_payload):
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer(sport="nfl").normalize(football_payload)
        result = tmp_db.write(ng)
        assert result["teams"] == 2
        assert result["period_scores"] == 8
        assert result["scoring_plays"] > 0

    def test_write_is_idempotent(self, tmp_db, football_payload):
        import duckdb
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        tmp_db.write(ng)
        tmp_db.write(ng)  # second write should not duplicate

        con = duckdb.connect(str(tmp_db.db_path))
        game_count = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        team_count = con.execute("SELECT COUNT(*) FROM team_game_stats").fetchone()[0]
        period_count = con.execute("SELECT COUNT(*) FROM period_scores").fetchone()[0]
        con.close()

        assert game_count == 1
        assert team_count == 2
        assert period_count == 8

    def test_join_games_and_teams(self, tmp_db, football_payload):
        import duckdb
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        tmp_db.write(ng)

        con = duckdb.connect(str(tmp_db.db_path))
        rows = con.execute("""
            SELECT g.title, t.side, t.team_name, t.score
            FROM games g
            JOIN team_game_stats t ON g.game_id = t.game_id
            ORDER BY t.side
        """).fetchall()
        con.close()

        assert len(rows) == 2
        # away comes first alphabetically
        assert rows[0][1] == "away"
        assert rows[1][1] == "home"

    def test_period_score_pivot_in_sql(self, tmp_db, football_payload):
        import duckdb
        from ingestion.mapper import GameNormalizer
        ng = GameNormalizer().normalize(football_payload)
        tmp_db.write(ng)

        con = duckdb.connect(str(tmp_db.db_path))
        rows = con.execute("""
            SELECT period,
                   MAX(CASE WHEN side = 'home' THEN points END) AS home_pts,
                   MAX(CASE WHEN side = 'away' THEN points END) AS away_pts
            FROM period_scores
            GROUP BY period
            ORDER BY period
        """).fetchall()
        con.close()

        assert len(rows) == 4   # 4 quarters
        # Q1: NE 0 – SEA 3
        assert rows[0] == (1, 0, 3)

    def test_write_schedule_games(self, tmp_db, schedule_games):
        import duckdb
        from ingestion.mapper import GameNormalizer
        from scripts.run_normalized import games_from_payload
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))

        normalizer = GameNormalizer(sport="nhl")
        for g in schedule_games:
            tmp_db.write(normalizer.normalize(g))

        con = duckdb.connect(str(tmp_db.db_path))
        game_count = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        con.close()

        assert game_count == len(schedule_games)
