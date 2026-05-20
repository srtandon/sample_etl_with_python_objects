"""
CLI entry point for the ingestion pipeline.

Usage examples:

    # Auto-detect adapter, run all cohorts + schedule:
    python scripts/run_ingestion.py --feed fixtures/feed_v1.json --trial-id TrialB

    # Explicitly select cohorts:
    python scripts/run_ingestion.py --feed fixtures/feed_v2.json --trial-id TrialB --cohorts A1 A4

    # Skip schedule processing:
    python scripts/run_ingestion.py --feed fixtures/feed_v1.json --trial-id TrialB --no-schedule

Run `pip install -e ".[dev]"` from the project root once so that the
`ingestion` package is importable without manual sys.path manipulation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running this script directly without `pip install -e .`
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ingestion.adapters.registry import pick_adapter
from ingestion.io import load_feed
from ingestion.jobs import ImportJob
from ingestion.processors.cohort import CohortProcessor
from ingestion.processors.schedule import ScheduleProcessor
from ingestion.sinks.duckdb_sink import DuckDBSink

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the ingestion pipeline against a feed file."
    )
    parser.add_argument(
        "--feed",
        required=True,
        help="Path to the feed file (.json, .yaml, or .yml).",
    )
    parser.add_argument(
        "--trial-id",
        required=True,
        help="Logical identifier for the trial (e.g. TrialB).",
    )
    parser.add_argument(
        "--cohorts",
        nargs="*",
        default=None,
        help='Cohort IDs to process. Omit to process all cohorts.',
    )
    parser.add_argument(
        "--no-schedule",
        action="store_true",
        help="Skip schedule processing.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Write Parquet output to this directory via DuckDB.  "
            "Creates output-dir/cohorts/{trial-id}.parquet and "
            "output-dir/schedules/{trial-id}.parquet.  "
            "Omit to run without persisting."
        ),
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    raw_payload = load_feed(args.feed)
    adapter = pick_adapter(raw_payload)
    print(f"Selected adapter: {type(adapter).__name__}")

    trial = adapter.to_trial_record(args.trial_id, raw_payload)
    print(f"Trial loaded: {trial.trial_id!r} ({trial.name}), study={trial.study}")
    print(f"Available cohorts: {trial.cohort_ids()}")

    cohort_ids = args.cohorts if args.cohorts else "all"
    processors = [CohortProcessor(cohort_ids)]
    if not args.no_schedule:
        processors.append(ScheduleProcessor())
    if args.output_dir:
        processors.append(DuckDBSink(output_dir=args.output_dir))
        print(f"DuckDB sink enabled → {args.output_dir}/")

    result = ImportJob(trial=trial, processors=processors).run()
    print(result.summary())
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
