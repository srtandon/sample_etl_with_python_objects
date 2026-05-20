"""
NormalizedDuckDBSink: writes a NormalizedGame into a persistent DuckDB database.

This is the production-grade storage layer.  Unlike FlatDuckDBSink (one wide
Parquet file per record), this sink writes to a proper relational schema where
tables are narrow, indexed by game_id, and joinable with SQL.

Schema
──────
  games            primary game metadata
  venues           deduplicated venue info (INSERT OR IGNORE)
  team_game_stats  home + away row per game
  period_scores    one row per period per side
  scoring_plays    one row per scoring event

Re-ingestion
────────────
Running the same game twice is safe: rows for that game_id are deleted from
mutable tables before re-inserting, so there are no duplicates.
Venues use INSERT OR IGNORE since venue info rarely changes.

Usage
─────
    sink = NormalizedDuckDBSink("output/sports.db")
    sink.setup()
    sink.write(game_normalizer.normalize(payload))

Query the database directly:
    duckdb output/sports.db "SELECT * FROM games"
    python scripts/run_sports.py --query-only
"""

from __future__ import annotations

import logging
from pathlib import Path

from ingestion.mapper import NormalizedGame

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# DDL — table definitions
# ──────────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS games (
    game_id      VARCHAR PRIMARY KEY,
    sr_id        VARCHAR,
    sport        VARCHAR,
    status       VARCHAR,
    title        VARCHAR,
    scheduled    VARCHAR,
    attendance   INTEGER,
    quarter      INTEGER,
    clock        VARCHAR,
    league_name  VARCHAR,
    season_year  INTEGER,
    season_type  VARCHAR,
    venue_id     VARCHAR,
    ingested_at  VARCHAR
);

CREATE TABLE IF NOT EXISTS venues (
    venue_id   VARCHAR PRIMARY KEY,
    name       VARCHAR,
    city       VARCHAR,
    state      VARCHAR,
    country    VARCHAR,
    capacity   INTEGER,
    surface    VARCHAR,
    roof_type  VARCHAR
);

CREATE TABLE IF NOT EXISTS team_game_stats (
    game_id       VARCHAR,
    side          VARCHAR,
    team_id       VARCHAR,
    team_name     VARCHAR,
    team_alias    VARCHAR,
    score         INTEGER,
    used_timeouts INTEGER,
    wins          INTEGER,
    losses        INTEGER,
    PRIMARY KEY (game_id, side)
);

CREATE TABLE IF NOT EXISTS period_scores (
    game_id  VARCHAR,
    side     VARCHAR,
    period   INTEGER,
    points   INTEGER,
    PRIMARY KEY (game_id, side, period)
);

CREATE TABLE IF NOT EXISTS scoring_plays (
    game_id      VARCHAR,
    play_seq     INTEGER,
    quarter      INTEGER,
    clock        VARCHAR,
    scoring_team VARCHAR,
    play_type    VARCHAR,
    home_points  INTEGER,
    away_points  INTEGER,
    PRIMARY KEY (game_id, play_seq)
);
"""


class NormalizedDuckDBSink:
    """
    Writes NormalizedGame rows to a persistent DuckDB database file.

    Args:
        db_path: Path to the DuckDB database file.  Created if it does not
                 exist.  Defaults to ``output/sports.db``.
    """

    def __init__(self, db_path: str | Path = "output/sports.db"):
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------
    # Schema setup — call once before writing
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create tables if they do not exist."""
        import duckdb

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self.db_path))
        # DuckDB has no executescript; split on the statement boundary.
        for stmt in _DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                con.execute(stmt)
        con.close()
        logger.info(f"Schema ready: {self.db_path}")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, normalized: NormalizedGame) -> dict:
        """
        Upsert all rows for one game into the database.

        Deletes existing rows for this game_id before inserting, so
        re-ingesting the same game is safe and idempotent.
        """
        import duckdb

        con = duckdb.connect(str(self.db_path))
        game_id = normalized.game_id

        # Remove stale rows for this game from mutable tables.
        for table in ("games", "team_game_stats", "period_scores", "scoring_plays"):
            con.execute(f"DELETE FROM {table} WHERE game_id = ?", [game_id])

        # games
        g = normalized.game
        con.execute(
            "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                g["game_id"], g["sr_id"], g["sport"], g["status"],
                g["title"], g["scheduled"], g["attendance"], g["quarter"],
                g["clock"], g["league_name"], g["season_year"],
                g["season_type"], g["venue_id"], g["ingested_at"],
            ],
        )

        # venues — INSERT OR IGNORE so we don't overwrite manually corrected data
        if normalized.venue and normalized.venue.get("venue_id"):
            v = normalized.venue
            con.execute(
                "INSERT OR IGNORE INTO venues VALUES (?,?,?,?,?,?,?,?)",
                [
                    v["venue_id"], v["name"], v["city"], v["state"],
                    v["country"], v["capacity"], v["surface"], v["roof_type"],
                ],
            )

        # team_game_stats
        for t in normalized.teams:
            con.execute(
                "INSERT INTO team_game_stats VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    t["game_id"], t["side"], t["team_id"], t["team_name"],
                    t["team_alias"], t["score"], t["used_timeouts"],
                    t["wins"], t["losses"],
                ],
            )

        # period_scores
        if normalized.period_scores:
            con.executemany(
                "INSERT INTO period_scores VALUES (?,?,?,?)",
                [
                    [r["game_id"], r["side"], r["period"], r["points"]]
                    for r in normalized.period_scores
                ],
            )

        # scoring_plays
        if normalized.scoring_plays:
            con.executemany(
                "INSERT INTO scoring_plays VALUES (?,?,?,?,?,?,?,?)",
                [
                    [
                        r["game_id"], r["play_seq"], r["quarter"], r["clock"],
                        r["scoring_team"], r["play_type"],
                        r["home_points"], r["away_points"],
                    ]
                    for r in normalized.scoring_plays
                ],
            )

        con.close()

        summary = {
            "game_id":       game_id,
            "teams":         len(normalized.teams),
            "period_scores": len(normalized.period_scores),
            "scoring_plays": len(normalized.scoring_plays),
        }
        logger.info(
            f"Wrote {game_id}: "
            f"{summary['teams']} teams, "
            f"{summary['period_scores']} period rows, "
            f"{summary['scoring_plays']} play rows"
        )
        return summary

    # ------------------------------------------------------------------
    # Convenience: run an ad-hoc query and print results
    # ------------------------------------------------------------------

    def query(self, sql: str) -> None:
        """Run *sql* against the database and print results (uses duckdb .show())."""
        import duckdb

        con = duckdb.connect(str(self.db_path))
        con.sql(sql).show()
        con.close()
