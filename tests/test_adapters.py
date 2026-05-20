"""
Adapter equivalence tests.

The central claim of this OOP design is that two completely different feed
structures — V1 (flat) and V2 (nested, repeated key name) — produce identical
TrialRecord objects when passed through their respective adapters.

If both tests pass, the portfolio story is proven in code: downstream processors
remain untouched even when the feed structure changes dramatically.
"""

import json
from pathlib import Path

import pytest

from ingestion.adapters.registry import pick_adapter
from ingestion.adapters.v1 import TrialFeedV1Adapter
from ingestion.adapters.v2 import TrialFeedV2Adapter
from ingestion.models import CohortRecord, ScheduleRecord, TrialRecord

FIXTURES = Path(__file__).parent.parent / "fixtures"
TRIAL_ID = "TrialB"


@pytest.fixture
def v1_payload():
    with (FIXTURES / "feed_v1.json").open() as f:
        data = json.load(f)
    data.pop("_comment", None)
    return data


@pytest.fixture
def v2_payload():
    with (FIXTURES / "feed_v2.json").open() as f:
        data = json.load(f)
    data.pop("_comment", None)
    # Strip nested _comment too, if present.
    if "trial" in data:
        data["trial"].pop("_comment", None)
    return data


# ---------------------------------------------------------------------------
# can_handle() detection
# ---------------------------------------------------------------------------


def test_v1_adapter_recognises_flat_payload(v1_payload):
    assert TrialFeedV1Adapter().can_handle(v1_payload) is True


def test_v2_adapter_recognises_nested_payload(v2_payload):
    assert TrialFeedV2Adapter().can_handle(v2_payload) is True


def test_v1_adapter_rejects_nested_payload(v2_payload):
    assert TrialFeedV1Adapter().can_handle(v2_payload) is False


def test_v2_adapter_rejects_flat_payload(v1_payload):
    assert TrialFeedV2Adapter().can_handle(v1_payload) is False


# ---------------------------------------------------------------------------
# Registry auto-selection
# ---------------------------------------------------------------------------


def test_registry_picks_v1_for_flat_payload(v1_payload):
    adapter = pick_adapter(v1_payload)
    assert isinstance(adapter, TrialFeedV1Adapter)


def test_registry_picks_v2_for_nested_payload(v2_payload):
    adapter = pick_adapter(v2_payload)
    assert isinstance(adapter, TrialFeedV2Adapter)


def test_registry_raises_for_unknown_payload():
    with pytest.raises(ValueError, match="No registered adapter"):
        pick_adapter({"unexpected_key": 42})


# ---------------------------------------------------------------------------
# Adapter equivalence — the portfolio punchline
# ---------------------------------------------------------------------------


def test_both_adapters_produce_equal_trial_records(v1_payload, v2_payload):
    """
    V1 and V2 feeds contain the same semantic data in different structures.
    Both adapters must produce an equal TrialRecord.  If this test passes,
    no processor or job code needs to change when the feed version changes.
    """
    v1_record = TrialFeedV1Adapter().to_trial_record(TRIAL_ID, v1_payload)
    v2_record = TrialFeedV2Adapter().to_trial_record(TRIAL_ID, v2_payload)

    assert v1_record.trial_id == v2_record.trial_id
    assert v1_record.name == v2_record.name
    assert v1_record.study == v2_record.study
    assert v1_record.sources == v2_record.sources
    assert v1_record.schedule == v2_record.schedule
    assert v1_record.cohorts == v2_record.cohorts


def test_both_adapters_produce_equal_cohort_totals(v1_payload, v2_payload):
    """
    Cross-check via computed values: total dose exposure per cohort must be
    identical regardless of which adapter was used.
    """
    v1_record = TrialFeedV1Adapter().to_trial_record(TRIAL_ID, v1_payload)
    v2_record = TrialFeedV2Adapter().to_trial_record(TRIAL_ID, v2_payload)

    for cid in v1_record.cohort_ids():
        assert v1_record.cohorts[cid].total_dose_exposure == \
               v2_record.cohorts[cid].total_dose_exposure, \
               f"Mismatch for cohort {cid!r}"


# ---------------------------------------------------------------------------
# TrialRecord content correctness (using v1 as the reference shape)
# ---------------------------------------------------------------------------


def test_v1_cohort_data_is_correct(v1_payload):
    record = TrialFeedV1Adapter().to_trial_record(TRIAL_ID, v1_payload)

    assert record.trial_id == TRIAL_ID
    assert record.name == "Hospital Trial 1"
    assert record.study == "A229"
    assert set(record.cohort_ids()) == {"A1", "A2", "A3", "A4"}

    a1 = record.cohorts["A1"]
    assert isinstance(a1, CohortRecord)
    assert a1.patient_count == 8
    assert a1.days == (1, 2, 14)
    assert a1.dose == 50.0
    assert a1.total_dose_exposure == 150.0  # 3 days × 50


def test_v1_schedule_is_correct(v1_payload):
    record = TrialFeedV1Adapter().to_trial_record(TRIAL_ID, v1_payload)
    assert isinstance(record.schedule, ScheduleRecord)
    assert record.schedule.cycle_length == 15
    assert record.schedule.duration == 6


def test_missing_cohorts_yields_empty_dict():
    payload = {"name": "Bare Trial", "study": "X", "sources": []}
    record = TrialFeedV1Adapter().to_trial_record("BT", payload)
    assert record.cohorts == {}
    assert record.schedule is None
