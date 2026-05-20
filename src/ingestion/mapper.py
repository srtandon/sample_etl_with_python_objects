"""
GameNormalizer: maps a raw sports game payload to a set of typed, narrow rows.

This is the production ingestion path — the alternative to flattening every
key into a wide Parquet file.

── Why not just flatten? ────────────────────────────────────────────────────

A football boxscore has ~15 scoring plays each with 7 fields, and 8 period
scores (4 quarters × 2 sides).  Flattening produces columns like:
    scoring_plays_0_clock, scoring_plays_0_description, scoring_plays_1_clock …
Across 500 games that is hundreds of mostly-NULL columns.  Storage cost
balloons, query performance degrades, and adding an 11th scoring play
silently adds 7 new columns to every file.

── The normalized schema ────────────────────────────────────────────────────

 games              1 row per game   ~12 columns
 venues             1 row per venue  ~10 columns  (deduplicated)
 team_game_stats    2 rows per game  ~8  columns  (home + away)
 period_scores      N rows per game  5   columns  (one per quarter/period/side)
 scoring_plays      N rows per game  7   columns  (one per scoring event)

Joining them is trivial in DuckDB:
    SELECT g.title, t.team_name, t.score
    FROM games g JOIN team_game_stats t ON g.game_id = t.game_id

── What the normalizer handles ──────────────────────────────────────────────

- Football boxscore: period scores as a list of {period_sequence, points}
  under each side's block; scoring_plays as a top-level list.
- Hockey/basketball schedule: games array wrapper; no period scores for
  future games (status != "closed").
- Missing fields (score, attendance) for scheduled/future games → None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Output dataclass
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class NormalizedGame:
    """
    All rows produced from one raw game payload, ready for the sink.

    Each attribute maps directly to one table in the target schema.
    """

    game: dict
    teams: list[dict] = field(default_factory=list)
    period_scores: list[dict] = field(default_factory=list)
    scoring_plays: list[dict] = field(default_factory=list)
    venue: dict | None = None

    @property
    def game_id(self) -> str:
        return self.game["game_id"]

    def summary(self) -> str:
        return (
            f"game={self.game_id!r}  "
            f"teams={len(self.teams)}  "
            f"periods={len(self.period_scores)}  "
            f"plays={len(self.scoring_plays)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Normalizer
# ──────────────────────────────────────────────────────────────────────────────


class GameNormalizer:
    """
    Extracts normalized, narrow-table rows from a raw sports game payload.

    Handles both completed games (with scores/period data) and scheduled
    games (with no scoring yet).  Designed to be sport-agnostic at the
    structural level — sport-specific logic should subclass or configure
    this class rather than adding conditionals inside it.

    Args:
        sport: Logical sport name stored in the ``games`` table for filtering.
    """

    def __init__(self, sport: str = "unknown"):
        self.sport = sport

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def normalize(self, payload: dict) -> NormalizedGame:
        """
        Map one raw game dict to a NormalizedGame.

        Callers iterating over a schedule's ``games`` array should call this
        once per element, not on the outer schedule payload.
        """
        ingested_at = datetime.now(timezone.utc).isoformat()
        game_id = payload.get("id", payload.get("game_id", ""))

        game_row = self._game_row(payload, game_id, ingested_at)
        team_rows = self._team_rows(payload, game_id)
        period_rows = self._period_rows(payload, game_id)
        play_rows = self._scoring_play_rows(payload, game_id)
        venue_row = self._venue_row(payload)

        result = NormalizedGame(
            game=game_row,
            teams=team_rows,
            period_scores=period_rows,
            scoring_plays=play_rows,
            venue=venue_row,
        )
        logger.info(f"Normalized: {result.summary()}")
        return result

    # ------------------------------------------------------------------
    # Extraction helpers — each maps to one target table
    # ------------------------------------------------------------------

    def _game_row(self, p: dict, game_id: str, ingested_at: str) -> dict:
        venue = p.get("venue") or {}
        league = p.get("league") or {}
        season = p.get("season") or {}
        season_type = season.get("type") or {}

        return {
            "game_id":      game_id,
            "sr_id":        p.get("sr_id"),
            "sport":        self.sport,
            "status":       p.get("status"),
            "title":        p.get("title"),
            "scheduled":    p.get("scheduled"),
            "attendance":   p.get("attendance"),
            "quarter":      p.get("quarter"),
            "clock":        p.get("clock"),
            "league_name":  league.get("name") or p.get("league"),
            "season_year":  season.get("year"),
            "season_type":  season_type.get("name"),
            "venue_id":     venue.get("id"),
            "ingested_at":  ingested_at,
        }

    def _team_rows(self, p: dict, game_id: str) -> list[dict]:
        rows = []
        for side in ("home", "away"):
            team = p.get(side)
            if not team:
                continue
            record = team.get("record") or {}
            rows.append({
                "game_id":      game_id,
                "side":         side,
                "team_id":      team.get("id"),
                "team_name":    team.get("name"),
                "team_alias":   team.get("alias"),
                "score":        team.get("score"),             # None if game not played
                "used_timeouts": team.get("used_timeouts"),    # football
                "wins":         record.get("wins"),            # hockey/basketball
                "losses":       record.get("losses"),
            })
        return rows

    def _period_rows(self, p: dict, game_id: str) -> list[dict]:
        """
        Extract per-period scores for both sides.

        Handles the Sports Radar list format:
            home.scoring_by_period: [{period_sequence: 1, points: 0}, ...]

        Returns [] if no scoring data is present (e.g. scheduled game).
        """
        rows = []
        for side in ("home", "away"):
            team = p.get(side) or {}
            periods = team.get("scoring_by_period") or []
            for entry in periods:
                rows.append({
                    "game_id": game_id,
                    "side":    side,
                    "period":  entry.get("period_sequence"),
                    "points":  entry.get("points"),
                })
        return rows

    def _scoring_play_rows(self, p: dict, game_id: str) -> list[dict]:
        plays = p.get("scoring_plays") or []
        return [
            {
                "game_id":      game_id,
                "play_seq":     i,
                "quarter":      play.get("quarter"),
                "clock":        play.get("clock"),
                "scoring_team": play.get("scoring_team"),
                "play_type":    play.get("play_type"),
                "home_points":  play.get("home_points"),
                "away_points":  play.get("away_points"),
            }
            for i, play in enumerate(plays)
            if play.get("scoring_team") is not None   # skip failed conversions
        ]

    def _venue_row(self, p: dict) -> dict | None:
        v = p.get("venue")
        if not v:
            return None
        return {
            "venue_id":   v.get("id"),
            "name":       v.get("name"),
            "city":       v.get("city"),
            "state":      v.get("state"),
            "country":    v.get("country"),
            "capacity":   v.get("capacity"),
            "surface":    v.get("surface"),
            "roof_type":  v.get("roof_type"),
        }
