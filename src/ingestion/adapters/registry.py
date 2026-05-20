"""
Adapter registry: auto-selects the right versioned adapter for any raw payload.

This replaces the if/elif ladder a procedural script would need every time a
new feed version is introduced.  To add a v3 adapter:

    1. Create src/ingestion/adapters/v3.py implementing TrialFeedAdapter.
    2. Add TrialFeedV3Adapter() to _ADAPTERS below (before less-specific adapters).
    3. Write a test in tests/test_adapters.py.

No processor, job, or script code changes.
"""

from __future__ import annotations

from ingestion.adapters.base import TrialFeedAdapter
from ingestion.adapters.v1 import TrialFeedV1Adapter
from ingestion.adapters.v2 import TrialFeedV2Adapter

# More-specific (newer) adapters must come before more-general ones.
_ADAPTERS: list[TrialFeedAdapter] = [
    TrialFeedV2Adapter(),
    TrialFeedV1Adapter(),
]


def pick_adapter(payload: dict) -> TrialFeedAdapter:
    """
    Return the first registered adapter that can handle the payload.

    Raises ValueError if no adapter recognises the structure, which is the
    signal to implement and register a new versioned adapter.
    """
    for adapter in _ADAPTERS:
        if adapter.can_handle(payload):
            return adapter
    raise ValueError(
        f"No registered adapter can handle a payload with top-level keys: "
        f"{list(payload.keys())}. "
        "Implement a new TrialFeedAdapter subclass and add it to registry._ADAPTERS."
    )
