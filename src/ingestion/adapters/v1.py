"""
V1 adapter — flat YAML/JSON structure.

Expected shape (one trial's slice of sample_ingestion_data.yaml):

    name: "Hospital Trial 1"
    study: "A229"
    cohorts:
      A1:
        patient_count: 8
        d: [1, 2, 14]
        dose: 50
    schedule:
      cycle_length: 15
      duration: 6
    sources: ["lab", "hospital"]

All keys appear at a single, predictable depth.  This is the "before" shape
that real pipelines start with before vendors start reshuffling their exports.
"""

from __future__ import annotations

from ingestion.adapters.base import TrialFeedAdapter
from ingestion.models import CohortRecord, ScheduleRecord, TrialRecord


class TrialFeedV1Adapter(TrialFeedAdapter):
    """Normalises the flat, first-version feed shape into a TrialRecord."""

    def can_handle(self, payload: dict) -> bool:
        # Flat shape: name and cohorts live at the top level, no wrapping key.
        return (
            isinstance(payload, dict)
            and "name" in payload
            and "cohorts" in payload
            and "trial" not in payload  # distinguish from v2
        )

    def to_trial_record(self, trial_id: str, payload: dict) -> TrialRecord:
        cohorts = {
            cid: CohortRecord(
                cohort_id=cid,
                patient_count=cdata.get("patient_count", 0),
                days=tuple(cdata.get("d", [])),
                dose=float(cdata.get("dose", 0)),
            )
            for cid, cdata in payload.get("cohorts", {}).items()
        }

        sched_raw = payload.get("schedule")
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
            name=payload.get("name", trial_id),
            study=payload.get("study", ""),
            cohorts=cohorts,
            schedule=schedule,
            sources=list(payload.get("sources", [])),
        )
