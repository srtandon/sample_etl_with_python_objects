"""
V2 adapter — nested structure with a repeated key name.

This adapter models the exact problem that motivated the OOP redesign: a
vendor changed the export format so that fields moved to a different nesting
depth, AND the word "trial" was reused as a key at two distinct levels
(outer wrapping object and inner study data block).

Expected shape:

    {
      "trial": {                       ← outer "trial": wraps the whole record
        "name": "Hospital Trial 1",
        "sources": ["lab", "hospital"],
        "schedule": { "cycle_length": 15, "duration": 6 },
        "trial": {                     ← inner "trial": same key, different meaning
          "study": "A229",
          "cohorts": {
            "A1": { "patient_count": 8, "d": [1, 2, 14], "dose": 50 }
          }
        }
      }
    }

The outer "trial" block holds display metadata and schedule.
The inner "trial" block holds study identity and cohort data.

Both this adapter and TrialFeedV1Adapter produce equal TrialRecord objects
from semantically identical data — see tests/test_adapters.py for the proof.
"""

from __future__ import annotations

from ingestion.adapters.base import TrialFeedAdapter
from ingestion.models import CohortRecord, ScheduleRecord, TrialRecord


class TrialFeedV2Adapter(TrialFeedAdapter):
    """
    Normalises the nested, repeated-key feed shape into a TrialRecord.

    The adapter absorbs all the structural chaos so that processors and jobs
    remain completely unchanged relative to the v1 pipeline.
    """

    def can_handle(self, payload: dict) -> bool:
        # V2 wraps everything inside a top-level "trial" key, which itself
        # contains another "trial" key for the inner study block.
        outer = payload.get("trial")
        return isinstance(outer, dict) and "trial" in outer

    def to_trial_record(self, trial_id: str, payload: dict) -> TrialRecord:
        outer = payload["trial"]
        inner = outer["trial"]  # same key name, but scoped to study data

        # Cohorts live in the inner block; fall back to outer if absent.
        cohorts_raw = inner.get("cohorts") or outer.get("cohorts", {})
        cohorts = {
            cid: CohortRecord(
                cohort_id=cid,
                patient_count=cdata.get("patient_count", 0),
                days=tuple(cdata.get("d", [])),
                dose=float(cdata.get("dose", 0)),
            )
            for cid, cdata in cohorts_raw.items()
        }

        # Schedule lives at the outer level; fall back to inner if absent.
        sched_raw = outer.get("schedule") or inner.get("schedule")
        schedule = (
            ScheduleRecord(
                cycle_length=sched_raw["cycle_length"],
                duration=sched_raw["duration"],
            )
            if sched_raw
            else None
        )

        return TrialRecord(
            trial_id=trial_id,
            name=outer.get("name", trial_id),
            study=inner.get("study", outer.get("study", "")),
            cohorts=cohorts,
            schedule=schedule,
            sources=list(outer.get("sources", inner.get("sources", []))),
        )
