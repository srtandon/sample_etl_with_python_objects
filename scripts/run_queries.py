"""
Run sample SQL queries over ingested Parquet files using DuckDB.

This script demonstrates the analytical payoff of the ingestion pipeline:
after running run_ingestion.py, the output Parquet files can be queried
with full SQL — window functions, cross-file JOINs, aggregations — without
a server, without exports, and without any additional infrastructure.

Usage:
    python scripts/run_queries.py                        # default output/ dir
    python scripts/run_queries.py --output-dir /some/path
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import duckdb
except ImportError:
    print("duckdb is required.  Install with: pip install duckdb")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent.parent


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query ingested Parquet files with DuckDB SQL."
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "output"),
        help="Directory containing cohorts/ and schedules/ Parquet files.",
    )
    return parser


def check_output_exists(output_dir: Path) -> None:
    cohort_files = list((output_dir / "cohorts").glob("*.parquet")) if (
        output_dir / "cohorts"
    ).exists() else []
    if not cohort_files:
        print("No Parquet files found in output/cohorts/.")
        print("Run the ingestion pipeline first, e.g.:")
        print(
            "  python scripts/run_ingestion.py "
            "--feed fixtures/feed_v1.json --trial-id TrialB --output-dir output"
        )
        sys.exit(1)


def section(title: str) -> None:
    border = "─" * 62
    print(f"\n{border}")
    print(f"  {title}")
    print(border)


def run_query(title: str, sql: str) -> None:
    section(title)
    duckdb.sql(sql).show()


def main() -> int:
    args = build_arg_parser().parse_args()
    output_dir = Path(args.output_dir)
    check_output_exists(output_dir)

    cohorts = str(output_dir / "cohorts" / "*.parquet")
    schedules = str(output_dir / "schedules" / "*.parquet")

    run_query(
        "All cohorts ranked by total dose exposure",
        f"""
        SELECT trial_id, cohort_id, patient_count, dose, total_dose_exposure
        FROM read_parquet('{cohorts}')
        ORDER BY total_dose_exposure DESC
        """,
    )

    run_query(
        "Patient headcount and dose burden per trial",
        f"""
        SELECT
            trial_id,
            SUM(patient_count)       AS total_patients,
            COUNT(cohort_id)         AS cohort_count,
            SUM(total_dose_exposure) AS trial_dose_burden
        FROM read_parquet('{cohorts}')
        GROUP BY trial_id
        ORDER BY total_patients DESC
        """,
    )

    run_query(
        "Schedule info joined to cohort summary",
        f"""
        SELECT
            s.trial_id,
            s.name,
            s.total_days,
            c.cohort_count,
            c.total_patients,
            ROUND(c.total_patients * 1.0 / c.cohort_count, 1) AS avg_patients_per_cohort
        FROM read_parquet('{schedules}') s
        JOIN (
            SELECT trial_id, COUNT(*) AS cohort_count, SUM(patient_count) AS total_patients
            FROM read_parquet('{cohorts}')
            GROUP BY trial_id
        ) c ON s.trial_id = c.trial_id
        """,
    )

    run_query(
        "Cohorts ranked within each trial (window function)",
        f"""
        SELECT
            trial_id,
            cohort_id,
            total_dose_exposure,
            RANK() OVER (
                PARTITION BY trial_id ORDER BY total_dose_exposure DESC
            ) AS rank_within_trial
        FROM read_parquet('{cohorts}')
        ORDER BY trial_id, rank_within_trial
        """,
    )

    run_query(
        "Ingestion audit: latest run per trial",
        f"""
        SELECT trial_id, MAX(ingested_at) AS last_ingested
        FROM read_parquet('{cohorts}')
        GROUP BY trial_id
        ORDER BY last_ingested DESC
        """,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
