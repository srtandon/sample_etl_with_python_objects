"""
Abstract base class for all versioned feed adapters.

Each adapter encodes the knowledge of *one* feed shape.  Downstream code
(processors, jobs) never sees raw dict paths — they depend only on TrialRecord.

Adding support for a new feed version means implementing this interface and
registering the adapter in registry.py.  No existing processor or job changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ingestion.models import TrialRecord


class TrialFeedAdapter(ABC):
    """
    Strategy interface for normalizing a raw feed payload into a TrialRecord.

    The two-method contract keeps adapters small and independently testable:
      - can_handle() is a cheap structural probe (no side effects).
      - to_trial_record() does the actual mapping.
    """

    @abstractmethod
    def can_handle(self, payload: dict) -> bool:
        """
        Return True if this adapter recognises the payload's structure.

        Called by the registry to select the right adapter without requiring
        callers to know which version a feed is.
        """

    @abstractmethod
    def to_trial_record(self, trial_id: str, payload: dict) -> TrialRecord:
        """
        Map a raw payload dict to a stable TrialRecord.

        Args:
            trial_id: Logical identifier for the trial (e.g. "TrialB").
            payload:  The raw dict slice that describes one trial's data.
        """
