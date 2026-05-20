"""
Processor tests.

Processors receive a TrialRecord and must behave identically regardless of
which adapter produced it.  These tests use hand-crafted TrialRecord objects
(no file I/O) to keep them fast and self-contained.
"""

import pytest

from ingestion.jobs import ImportJob, PipelineResult
from ingestion.models import CohortRecord, ScheduleRecord, TrialRecord
from ingestion.processors.cohort import CohortProcessor
from ingestion.processors.schedule import ScheduleProcessor


# ---------------------------------------------------------------------------
# Shared fixtures
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
            "A3": CohortRecord("A3", patient_count=4,  days=(1, 2, 14), dose=75.0),
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
# CohortProcessor
# ---------------------------------------------------------------------------


def test_cohort_processor_specific_ids(trial):
    result = CohortProcessor(["A1", "A4"]).run(trial)
    assert result == {
        "A1": 150.0,  # 3 days × 50
        "A4": 75.0,   # 3 days × 25
    }


def test_cohort_processor_all(trial):
    result = CohortProcessor("all").run(trial)
    assert set(result.keys()) == {"A1", "A2", "A3", "A4"}
    assert result["A2"] == 300.0  # 3 days × 100
    assert result["A3"] == 225.0  # 3 days × 75


def test_cohort_processor_missing_cohort_is_skipped(trial):
    """An unknown cohort ID is warned about and skipped, not raised."""
    result = CohortProcessor(["A1", "Z9"]).run(trial)
    assert "A1" in result
    assert "Z9" not in result


def test_cohort_processor_patient_count(trial):
    assert CohortProcessor("all").patient_count(trial, "A2") == 10


def test_cohort_processor_patient_count_missing_raises(trial):
    with pytest.raises(KeyError, match="Z9"):
        CohortProcessor("all").patient_count(trial, "Z9")


# ---------------------------------------------------------------------------
# ScheduleProcessor
# ---------------------------------------------------------------------------


def test_schedule_processor_returns_expected_dict(trial):
    result = ScheduleProcessor().run(trial)
    assert result == {"cycle_length": 15, "duration": 6, "total_days": 90}


def test_schedule_processor_returns_none_when_no_schedule(trial_no_schedule):
    result = ScheduleProcessor().run(trial_no_schedule)
    assert result is None


# ---------------------------------------------------------------------------
# ImportJob
# ---------------------------------------------------------------------------


def test_import_job_runs_all_processors(trial):
    job = ImportJob(
        trial=trial,
        processors=[CohortProcessor(["A1", "A4"]), ScheduleProcessor()],
    )
    result = job.run()

    assert isinstance(result, PipelineResult)
    assert result.success is True
    assert result.trial_id == "TrialB"
    assert "CohortProcessor" in result.data
    assert "ScheduleProcessor" in result.data
    assert result.data["CohortProcessor"] == {"A1": 150.0, "A4": 75.0}


def test_import_job_captures_processor_error(trial):
    class BrokenProcessor:
        def run(self, trial):
            raise RuntimeError("simulated failure")

    job = ImportJob(trial=trial, processors=[BrokenProcessor()])
    result = job.run()

    assert result.success is False
    assert any("simulated failure" in e for e in result.errors)


def test_pipeline_result_summary_includes_trial_id(trial):
    result = ImportJob(trial=trial, processors=[ScheduleProcessor()]).run()
    assert "TrialB" in result.summary()
