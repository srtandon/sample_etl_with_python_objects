"""
Normalized sports ingestion: GameNormalizer → NormalizedDuckDBSink.

Handles both formats from your real fixtures:
  - Single-game boxscore (football_game_boxscore.json)
  - Schedule with games array (schedule.json)

Usage:
    # Ingest boxscore
    python scripts/run_normalized.py --feed fixtures/football_game_boxscore.json --sport nfl

    # Ingest full schedule (iterates over games array automatically)
    python scripts/run_normalized.py --feed fixtures/schedule.json --sport nhl

    # Query the database after ingesting
    python scripts/run_normalized.py --query-only

    # Reset the database and re-ingest (safe, idempotent)
    python scripts/run_normalized.py --feed fixtures/football_game_boxscore.json --sport nfl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ingestion.mapper import GameNormalizer
from ingestion.sinks.normalized_sink import NormalizedDuckDBSink

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_DB = str(PROJECT_ROOT / "output" / "sports.db")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize sports feeds into a relational DuckDB schema."
    )
    parser.add_argument("--feed", help="Path to feed JSON file.")
    parser.add_argument("--sport", default="unknown", help="Sport label (e.g. nfl, nhl).")
    parser.add_argument("--db", default=DEFAULT_DB, help="DuckDB database path.")
    parser.add_argument(
        "--query-only",
        action="store_true",
        help="Skip ingestion and just run the sample queries.",
    )
    return parser


def games_from_payload(payload: dict) -> list[dict]:
    """
    Return a list of individual game dicts from the payload.

    Handles:
      - A schedule payload with a top-level 'games' array.
      - A single boxscore payload (returned as a one-item list).
    """
    if "games" in payload and isinstance(payload["games"], list):
        # Schedule format: attach top-level league/season to each game
        # so the normalizer can find them.
        league = payload.get("league", {})
        season = payload.get("season", {})
        games = []
        for g in payload["games"]:
            g = dict(g)
            g.setdefault("league", league)
            g.setdefault("season", season)
            games.append(g)
        return games
    # Single boxscore format
    return [payload]


def run_queries(sink: NormalizedDuckDBSink) -> None:
    print("\n─── Game results ────────────────────────────────────────────")
    sink.query("""
        SELECT g.title, g.status, g.scheduled,
               home.team_name AS home_team, home.score AS home_score,
               away.team_name AS away_team, away.score AS away_score
        FROM games g
        LEFT JOIN team_game_stats home ON g.game_id = home.game_id AND home.side = 'home'
        LEFT JOIN team_game_stats away ON g.game_id = away.game_id AND away.side = 'away'
        ORDER BY g.scheduled
    """)

    print("\n─── Period-by-period scores ─────────────────────────────────")
    sink.query("""
        SELECT g.title, p.side, p.period, p.points
        FROM period_scores p
        JOIN games g ON p.game_id = g.game_id
        ORDER BY g.title, p.period, p.side
    """)

    print("\n─── Scoring play summary by type ────────────────────────────")
    sink.query("""
        SELECT play_type, COUNT(*) AS plays
        FROM scoring_plays
        GROUP BY play_type
        ORDER BY plays DESC
    """)

    print("\n─── Venues used ─────────────────────────────────────────────")
    sink.query("""
        SELECT v.name, v.city, v.state, v.capacity, v.surface, COUNT(g.game_id) AS games_played
        FROM venues v
        LEFT JOIN games g ON v.venue_id = g.venue_id
        GROUP BY v.venue_id, v.name, v.city, v.state, v.capacity, v.surface
        ORDER BY games_played DESC
    """)


def main() -> int:
    args = build_arg_parser().parse_args()
    sink = NormalizedDuckDBSink(db_path=args.db)
    sink.setup()

    if args.query_only:
        run_queries(sink)
        return 0

    if not args.feed:
        print("--feed is required unless --query-only is set.")
        return 1

    feed_path = Path(args.feed)
    if not feed_path.exists():
        print(f"Feed file not found: {feed_path}")
        return 1

    with feed_path.open() as f:
        payload = json.load(f)

    games = games_from_payload(payload)
    normalizer = GameNormalizer(sport=args.sport)

    print(f"Ingesting {len(games)} game(s) from {feed_path.name} …")
    for game_payload in games:
        normalized = normalizer.normalize(game_payload)
        sink.write(normalized)

    print(f"\nIngestion complete → {args.db}")
    run_queries(sink)
    return 0


if __name__ == "__main__":
    sys.exit(main())
