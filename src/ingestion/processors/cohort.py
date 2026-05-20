"""
CohortProcessor: computes per-cohort dose exposure for a TrialRecord.

Processors receive a stable TrialRecord — they have no knowledge of (and no
dependency on) the raw feed structure or which adapter was used to produce it.
"""

from __future__ import annotations

import logging
from typing import Sequence

from ingestion.models import TrialRecord

logger = logging.getLogger(__name__)


class CohortProcessor:
    """
    Calculates total dose exposure for a configurable set of cohorts.

    Args:
        cohort_ids: Either the string ``"all"`` to include every cohort in the
                    trial, or an explicit list of cohort ID strings.
    """

    def __init__(self, cohort_ids: str | Sequence[str]):
        self.cohort_ids = cohort_ids

    def run(self, trial: TrialRecord) -> dict[str, float]:
        """
        Return a mapping of cohort_id → total dose exposure.

        Total dose exposure = number of administration days × per-day dose.
        Cohort IDs present in the config but absent in the trial data are
        logged as warnings and skipped rather than raising hard errors.
        """
        ids_to_process = (
            list(trial.cohorts.keys())
            if self.cohort_ids == "all"
            else list(self.cohort_ids)
        )

        result: dict[str, float] = {}
        for cid in ids_to_process:
            cohort = trial.cohorts.get(cid)
            if cohort is None:
                logger.warning(
                    f"Cohort {cid!r} requested but not found in {trial.trial_id!r}. "
                    f"Available: {trial.cohort_ids()}"
                )
                continue
            result[cid] = cohort.total_dose_exposure
            logger.info(f"{trial.trial_id}/{cid}: total_dose_exposure={result[cid]}")

        return result

    def patient_count(self, trial: TrialRecord, cohort_id: str) -> int:
        """Return the patient count for a single cohort."""
        return trial.get_cohort(cohort_id).patient_count
