"""
ScheduleProcessor: extracts and validates the trial schedule from a TrialRecord.
"""

from __future__ import annotations

import logging
from typing import Optional

from ingestion.models import TrialRecord

logger = logging.getLogger(__name__)


class ScheduleProcessor:
    """
    Fetches schedule information from a TrialRecord and returns a plain dict
    suitable for logging or downstream handoff.

    When the trial has no schedule configured the processor returns None and
    logs an informational message rather than raising — callers decide how to
    treat the absence.
    """

    def run(self, trial: TrialRecord) -> Optional[dict]:
        if trial.schedule is None:
            logger.info(f"{trial.trial_id}: no schedule configured — skipping.")
            return None

        sched = trial.schedule
        result = {
            "cycle_length": sched.cycle_length,
            "duration": sched.duration,
            "total_days": sched.cycle_length * sched.duration,
        }
        logger.info(f"{trial.trial_id}: schedule collected — {result}")
        return result
