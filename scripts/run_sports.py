"""
Sports ingestion demo: JsonFlattener → SchemaResolver → FlatDuckDBSink.

This script demonstrates the 'unknown shape' half of the portfolio:

  - JsonFlattener walks any nested JSON, tracking parent-key context
    automatically so 'score' becomes 'home_score' or 'periods_1_home_score'
    depending on where it appears in the tree.

  - SchemaResolver maps the raw composite keys to stable canonical names,
    handling the case where the API vendor reorders nesting between versions
    (e.g. league.region in v1 vs region.league in v2).

  - FlatDuckDBSink writes the resolved flat record to Parquet with a
    schema inferred from the data — no schema declaration required.

Usage:
    # Ingest both feed versions (same game, different nesting)
    python scripts/run_sports.py --feed fixtures/sports_game.json
    python scripts/run_sports.py --feed fixtures/sports_game_v2.json

    # Then query the results
    python scripts/run_queries.py --output-dir output
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ingestion.flattener import JsonFlattener, SchemaResolver, flatten_to_record
from ingestion.sinks.flat_sink import FlatDuckDBSink

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ──────────────────────────────────────────────────────────────────────────────
# Resolver rules shared across all sports feed versions.
#
# v1 produces: league_name, league_region, league_season
# v2 produces: region_name, region_league_name, region_league_season
#
# Both map to the same canonical keys so downstream SQL queries are unchanged.
# ──────────────────────────────────────────────────────────────────────────────

SPORTS_RESOLVER = SchemaResolver(
    rules={
        # league name
        "league_name":          "league_name",        # v1 pass-through
        "region_league_name":   "league_name",        # v2 → canonical

        # region / conference name
        "league_region":        "region_name",        # v1 → canonical
        "region_name":          "region_name",        # v2 pass-through

        # season
        "league_season":        "season",             # v1 → canonical
        "region_league_season": "season",             # v2 → canonical

        # convenience: 'team' is ambiguous — be explicit
        "home_team":            "home_team_name",
        "away_team":            "away_team_name",
    }
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Flatten a sports JSON feed and write to Parquet via DuckDB."
    )
    parser.add_argument(
        "--feed",
        required=True,
        help="Path to the sports JSON feed file.",
    )
    parser.add_argument(
        "--source",
        default="nba_games",
        help="Logical source name used as the output sub-directory (default: nba_games).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Root directory for Parquet output (default: output/).",
    )
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="Skip schema resolution and write raw composite keys.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    feed_path = Path(args.feed)
    if not feed_path.exists():
        print(f"Feed file not found: {feed_path}")
        return 1

    with feed_path.open() as f:
        payload = json.load(f)
    payload.pop("_comment", None)

    record_id = str(payload.get("game_id", feed_path.stem))
    resolver = None if args.no_resolve else SPORTS_RESOLVER

    record = flatten_to_record(
        payload=payload,
        record_id=record_id,
        source=args.source,
        resolver=resolver,
    )

    # Show what flattening produced before writing
    print(f"\nFlattened {len(record)} fields from {feed_path.name}:")
    for k, v in sorted(record.fields.items()):
        print(f"  {k:40s} = {v!r}")

    sink = FlatDuckDBSink(output_dir=args.output_dir)
    summary = sink.write(record)
    print(f"\nWrote {summary['fields_written']} fields → {summary['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
