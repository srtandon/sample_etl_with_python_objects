"""
DuckDBSink: persists a TrialRecord to Parquet files using DuckDB as the engine.

Why this combination?
  - Parquet is columnar, compact, and readable by virtually every data tool
    (pandas, polars, Spark, Athena, BigQuery external tables, etc.).
  - DuckDB is an in-process analytical SQL engine — no server, no credentials,
    no configuration.  It writes Parquet natively and can query it back with
    full ANSI SQL including window functions and cross-file wildcards.

Output layout (one file per trial, overwritten on each run):

    output/
      cohorts/
        TrialB.parquet       ← one row per cohort
      schedules/
        TrialB.parquet       ← one row per trial

Querying across all ingested trials is then a single SQL statement:

    SELECT * FROM read_parquet('output/cohorts/*.parquet')

Because DuckDBSink exposes a run(trial) method it satisfies the Processor
protocol in jobs.py — no changes to ImportJob required.  Add it to the
processor list and it runs alongside CohortProcessor / ScheduleProcessor.

Cohort schema
─────────────
trial_id            VARCHAR
cohort_id           VARCHAR
patient_count       INTEGER
days                VARCHAR    (JSON-encoded list for broad tool compatibility)
day_count           INTEGER
dose                DOUBLE
total_dose_exposure DOUBLE
ingested_at         VARCHAR    (ISO-8601 UTC timestamp)

Schedule schema
───────────────
trial_id            VARCHAR
name                VARCHAR
study               VARCHAR
cycle_length        INTEGER
duration            INTEGER
total_days          INTEGER    (cycle_length × duration)
sources             VARCHAR    (JSON-encoded list)
ingested_at         VARCHAR
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ingestion.models import TrialRecord

logger = logging.getLogger(__name__)


class DuckDBSink:
    """
    Writes a TrialRecord to Parquet via DuckDB.

    Implements the Processor protocol so it can be passed directly to ImportJob
    alongside any other processors — no changes to job orchestration needed.

    Args:
        output_dir: Directory for Parquet output.  Sub-directories ``cohorts/``
                    and ``schedules/`` are created automatically.  Defaults to
                    ``output/`` relative to the project root; override for tests
                    or alternate environments.
    """

    def __init__(self, output_dir: str | Path = "output"):
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # Processor protocol
    # ------------------------------------------------------------------

    def run(self, trial: TrialRecord) -> dict:
        """
        Persist cohort and schedule data for *trial* as Parquet files.

        Returns a summary dict that lands in PipelineResult.data["DuckDBSink"]
        so callers can confirm what was written.
        """
        try:
            import duckdb
        except ImportError as exc:
            raise ImportError(
                "duckdb is required for DuckDBSink.  "
                "Install it with: pip install duckdb"
            ) from exc

        cohorts_dir = self.output_dir / "cohorts"
        schedules_dir = self.output_dir / "schedules"
        cohorts_dir.mkdir(parents=True, exist_ok=True)
        schedules_dir.mkdir(parents=True, exist_ok=True)

        ingested_at = datetime.now(timezone.utc).isoformat()
        con = duckdb.connect()

        cohort_path = self._write_cohorts(con, trial, cohorts_dir, ingested_at)
        schedule_path = self._write_schedule(con, trial, schedules_dir, ingested_at)

        con.close()

        summary = {
            "cohorts_written": len(trial.cohorts),
            "cohort_path": cohort_path,
            "schedule_path": schedule_path,
        }
        logger.info(
            f"{trial.trial_id}: wrote {summary['cohorts_written']} cohort row(s) "
            f"→ {cohort_path}"
        )
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_cohorts(
        self,
        con: object,
        trial: TrialRecord,
        cohorts_dir: Path,
        ingested_at: str,
    ) -> str:
        con.execute("""
            CREATE TABLE cohorts (
                trial_id            VARCHAR,
                cohort_id           VARCHAR,
                patient_count       INTEGER,
                days                VARCHAR,
                day_count           INTEGER,
                dose                DOUBLE,
                total_dose_exposure DOUBLE,
                ingested_at         VARCHAR
            )
        """)

        rows = [
            (
                trial.trial_id,
                c.cohort_id,
                c.patient_count,
                json.dumps(list(c.days)),
                len(c.days),
                c.dose,
                c.total_dose_exposure,
                ingested_at,
            )
            for c in trial.cohorts.values()
        ]
        con.executemany("INSERT INTO cohorts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

        path = str(cohorts_dir / f"{trial.trial_id}.parquet")
        con.execute(f"COPY cohorts TO '{path}' (FORMAT PARQUET)")
        return path

    def _write_schedule(
        self,
        con: object,
        trial: TrialRecord,
        schedules_dir: Path,
        ingested_at: str,
    ) -> str:
        con.execute("""
            CREATE TABLE schedules (
                trial_id     VARCHAR,
                name         VARCHAR,
                study        VARCHAR,
                cycle_length INTEGER,
                duration     INTEGER,
                total_days   INTEGER,
                sources      VARCHAR,
                ingested_at  VARCHAR
            )
        """)

        sched = trial.schedule
        con.execute(
            "INSERT INTO schedules VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trial.trial_id,
                trial.name,
                trial.study,
                sched.cycle_length if sched else None,
                sched.duration if sched else None,
                (sched.cycle_length * sched.duration) if sched else None,
                json.dumps(trial.sources),
                ingested_at,
            ],
        )

        path = str(schedules_dir / f"{trial.trial_id}.parquet")
        con.execute(f"COPY schedules TO '{path}' (FORMAT PARQUET)")
        return path
