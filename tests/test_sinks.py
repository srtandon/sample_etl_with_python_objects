"""
DuckDBSink tests.

Covers:
  - Parquet files are created in the expected locations
  - Cohort data round-trips correctly (write → read back via DuckDB)
  - Schedule data round-trips correctly
  - Trials with no schedule produce NULL schedule columns, not an error
  - DuckDBSink satisfies the Processor protocol (usable in ImportJob)
  - Summary dict returned by run() contains expected keys
"""

from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")

from ingestion.jobs import ImportJob
from ingestion.models import CohortRecord, ScheduleRecord, TrialRecord
from ingestion.sinks.duckdb_sink import DuckDBSink


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trial():
    return TrialRecord(
        trial_id="TrialB",
        name="Hospital Trial 1",
        study="A229",
        cohorts={
            "A1": CohortRecord("A1", patient_count=8,  days=(1, 2, 14), dose=50.0),
            "A2": CohortRecord("A2", patient_count=10, days=(1, 2, 14), dose=100.0),
            "A4": CohortRecord("A4", patient_count=12, days=(1, 2, 14), dose=25.0),
        },
        schedule=ScheduleRecord(cycle_length=15, duration=6),
        sources=["lab", "hospital"],
    )


@pytest.fixture
def trial_no_schedule(trial):
    trial.schedule = None
    return trial


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------


def test_parquet_files_are_created(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)

    assert (tmp_path / "cohorts" / "TrialB.parquet").exists()
    assert (tmp_path / "schedules" / "TrialB.parquet").exists()


def test_output_dirs_are_created_automatically(trial, tmp_path):
    nested = tmp_path / "deep" / "nested"
    DuckDBSink(output_dir=nested).run(trial)
    assert (nested / "cohorts" / "TrialB.parquet").exists()


# ---------------------------------------------------------------------------
# Round-trip: cohorts
# ---------------------------------------------------------------------------


def test_cohort_row_count(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)
    path = str(tmp_path / "cohorts" / "TrialB.parquet")
    count = duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{path}')").fetchone()[0]
    assert count == len(trial.cohorts)


def test_cohort_columns_present(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)
    path = str(tmp_path / "cohorts" / "TrialB.parquet")
    cols = {
        row[0]
        for row in duckdb.sql(
            f"DESCRIBE SELECT * FROM read_parquet('{path}')"
        ).fetchall()
    }
    assert {"trial_id", "cohort_id", "patient_count", "dose",
            "total_dose_exposure", "day_count", "days", "ingested_at"} <= cols


def test_cohort_values_correct(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)
    path = str(tmp_path / "cohorts" / "TrialB.parquet")

    row = duckdb.sql(
        f"SELECT cohort_id, patient_count, dose, total_dose_exposure, day_count "
        f"FROM read_parquet('{path}') WHERE cohort_id = 'A1'"
    ).fetchone()

    assert row is not None
    cohort_id, patient_count, dose, total_dose_exposure, day_count = row
    assert cohort_id == "A1"
    assert patient_count == 8
    assert dose == 50.0
    assert total_dose_exposure == 150.0   # 3 days × 50
    assert day_count == 3


def test_all_cohorts_present_in_parquet(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)
    path = str(tmp_path / "cohorts" / "TrialB.parquet")
    ids = {
        row[0]
        for row in duckdb.sql(
            f"SELECT cohort_id FROM read_parquet('{path}')"
        ).fetchall()
    }
    assert ids == set(trial.cohorts.keys())


# ---------------------------------------------------------------------------
# Round-trip: schedule
# ---------------------------------------------------------------------------


def test_schedule_values_correct(trial, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial)
    path = str(tmp_path / "schedules" / "TrialB.parquet")

    row = duckdb.sql(
        f"SELECT trial_id, name, study, cycle_length, duration, total_days "
        f"FROM read_parquet('{path}')"
    ).fetchone()

    assert row is not None
    trial_id, name, study, cycle_length, duration, total_days = row
    assert trial_id == "TrialB"
    assert name == "Hospital Trial 1"
    assert study == "A229"
    assert cycle_length == 15
    assert duration == 6
    assert total_days == 90


def test_no_schedule_produces_null_columns(trial_no_schedule, tmp_path):
    DuckDBSink(output_dir=tmp_path).run(trial_no_schedule)
    path = str(tmp_path / "schedules" / "TrialB.parquet")

    row = duckdb.sql(
        f"SELECT cycle_length, duration, total_days FROM read_parquet('{path}')"
    ).fetchone()

    assert row == (None, None, None)


# ---------------------------------------------------------------------------
# Cross-file SQL (the real portfolio value)
# ---------------------------------------------------------------------------


def test_cross_file_join(tmp_path):
    """
    Two trials are ingested independently and then joined in a single SQL query —
    the same query pattern used in run_queries.py and sample_queries.sql.
    """
    trial_b = TrialRecord(
        trial_id="TrialB", name="Hospital Trial 1", study="A229",
        cohorts={"A1": CohortRecord("A1", 8, (1, 2, 14), 50.0)},
        schedule=ScheduleRecord(cycle_length=15, duration=6),
        sources=["lab"],
    )
    trial_c = TrialRecord(
        trial_id="TrialC", name="Home Trial 2", study="A229",
        cohorts={
            "A2": CohortRecord("A2", 3, (1, 2, 8, 22, 29), 10.0),
            "B":  CohortRecord("B",  9, (1, 2, 8, 22, 29), 20.0),
        },
        schedule=ScheduleRecord(cycle_length=30, duration=12),
        sources=["hospital"],
    )
    sink = DuckDBSink(output_dir=tmp_path)
    sink.run(trial_b)
    sink.run(trial_c)

    cohorts = str(tmp_path / "cohorts" / "*.parquet")
    schedules = str(tmp_path / "schedules" / "*.parquet")

    # Cross-file JOIN: schedule info + cohort counts
    rows = duckdb.sql(f"""
        SELECT s.trial_id, s.total_days, c.cohort_count
        FROM read_parquet('{schedules}') s
        JOIN (
            SELECT trial_id, COUNT(*) AS cohort_count
            FROM read_parquet('{cohorts}')
            GROUP BY trial_id
        ) c ON s.trial_id = c.trial_id
        ORDER BY s.trial_id
    """).fetchall()

    assert len(rows) == 2
    b_row = next(r for r in rows if r[0] == "TrialB")
    c_row = next(r for r in rows if r[0] == "TrialC")
    assert b_row[1] == 90    # 15 × 6
    assert c_row[1] == 360   # 30 × 12
    assert b_row[2] == 1
    assert c_row[2] == 2


# ---------------------------------------------------------------------------
# Integration with ImportJob
# ---------------------------------------------------------------------------


def test_duckdb_sink_works_inside_import_job(trial, tmp_path):
    """DuckDBSink satisfies the Processor protocol and runs cleanly inside ImportJob."""
    from ingestion.processors.cohort import CohortProcessor
    from ingestion.processors.schedule import ScheduleProcessor

    result = ImportJob(
        trial=trial,
        processors=[
            CohortProcessor("all"),
            ScheduleProcessor(),
            DuckDBSink(output_dir=tmp_path),
        ],
    ).run()

    assert result.success is True
    assert "DuckDBSink" in result.data
    assert result.data["DuckDBSink"]["cohorts_written"] == 3


# ---------------------------------------------------------------------------
# run() return value
# ---------------------------------------------------------------------------


def test_run_returns_summary_dict(trial, tmp_path):
    summary = DuckDBSink(output_dir=tmp_path).run(trial)
    assert summary["cohorts_written"] == len(trial.cohorts)
    assert Path(summary["cohort_path"]).exists()
    assert Path(summary["schedule_path"]).exists()
