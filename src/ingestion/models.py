"""
Canonical domain model for a clinical-trial ingestion pipeline.

After a raw feed passes through a versioned adapter, all downstream code
works exclusively with these stable dataclasses — never with raw dict paths.
This is the key OOP insight: separate *where data lives* (adapters) from
*what data means* (models) and *what to do with it* (processors).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CohortRecord:
    """Immutable representation of a single cohort within a trial."""

    cohort_id: str
    patient_count: int
    days: tuple[int, ...]  # tuple keeps it hashable / frozen-safe
    dose: float

    @property
    def total_dose_exposure(self) -> float:
        """Number of administration days multiplied by per-day dose."""
        return len(self.days) * self.dose


@dataclass(frozen=True)
class ScheduleRecord:
    """Immutable trial schedule descriptor."""

    cycle_length: int  # days per cycle
    duration: int      # total number of cycles


@dataclass
class TrialRecord:
    """
    Stable, adapter-independent representation of a clinical trial.

    All processors and jobs depend only on this class, so they remain
    unchanged when the upstream JSON structure shifts between versions.
    """

    trial_id: str
    name: str
    study: str
    cohorts: dict[str, CohortRecord] = field(default_factory=dict)
    schedule: Optional[ScheduleRecord] = None
    sources: list[str] = field(default_factory=list)

    def cohort_ids(self) -> list[str]:
        return list(self.cohorts.keys())

    def get_cohort(self, cohort_id: str) -> CohortRecord:
        try:
            return self.cohorts[cohort_id]
        except KeyError:
            raise KeyError(
                f"Cohort {cohort_id!r} not found in trial {self.trial_id!r}. "
                f"Available: {self.cohort_ids()}"
            )
