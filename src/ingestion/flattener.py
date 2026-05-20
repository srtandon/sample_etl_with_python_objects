"""
JsonFlattener, SchemaResolver, and FlatRecord.

This module solves the problem that motivated the original project:
sports (or any deeply nested) API responses where the same key — 'score',
'players', 'fouls' — appears at multiple nesting depths, and the nesting
ORDER itself shifts between API versions.

A procedural approach requires tracking parent keys manually in every
parsing function.  Here, parent-key context is encapsulated in the
recursive walker's call state, and schema normalization is isolated in
a separate, composable SchemaResolver.

── The two problems this handles ───────────────────────────────────────────

Problem 1 — repeated keys at different depths
    {"home": {"score": 108}, "periods": {"1": {"home": {"score": 28}}}}

    The key "score" appears 10 times in a 4-period game.  JsonFlattener
    carries the parent-key stack so each becomes unique:
        home_score          = 108
        periods_1_home_score = 28

Problem 2 — nesting order changes between feed versions
    v1: {"league": {"name": "NBA", "region": "West"}}
    v2: {"region": {"name": "West", "league": {"name": "NBA"}}}

    v1 flattens to: league_name, league_region
    v2 flattens to: region_name, region_league_name

    One SchemaResolver with two rules maps both to the same canonical keys:
        league_name       → league_name   (v1 pass-through)
        region_league_name → league_name  (v2 mapped)

    No new adapter class needed — just a resolver rule.

── Contrast with the TrialFeedAdapter approach ──────────────────────────────

TrialFeedV1Adapter / V2Adapter are best when you have a *small number of
known shapes* with deeply different structures.  JsonFlattener is better
when the shape is *unknown or continuously shifting* — you let the tree
walk produce raw composite keys, then resolve them to canonical names.

Both approaches produce the same DuckDB-queryable output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# FlatRecord
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class FlatRecord:
    """
    Schema-agnostic record produced by JsonFlattener.

    Contrast with TrialRecord (fixed named fields) — FlatRecord is used when
    the incoming JSON structure is unknown, dynamic, or changes per sport/
    endpoint.  All discovered fields live in ``fields``; identity and source
    metadata are kept separately so sinks can partition output by source.
    """

    record_id: str
    source: str
    fields: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def keys(self) -> Any:
        return self.fields.keys()

    def __len__(self) -> int:
        return len(self.fields)

    def __repr__(self) -> str:
        return (
            f"FlatRecord(record_id={self.record_id!r}, "
            f"source={self.source!r}, fields={len(self.fields)})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# JsonFlattener
# ──────────────────────────────────────────────────────────────────────────────


class JsonFlattener:
    """
    Recursively walks a nested dict and produces a flat {composite_key: value}
    dict by joining parent keys with a separator.

    This is the OOP encapsulation of what procedural sports-API parsers do
    with manual key-tracking in nested for-loops — but the parent-key context
    is maintained automatically in the recursive call stack.

    Args:
        separator:  String used to join key segments.  Default ``"_"``.
        max_depth:  Guard against pathological nesting; payload beyond this
                    depth is stored as a raw value.  Default 20.
        skip_keys:  Set of top-level or intermediate keys to exclude from
                    output (e.g. ``{"_comment"}``).

    Example::

        flat = JsonFlattener().flatten({
            "home": {"score": 108, "team": "Lakers"},
            "periods": {"1": {"home": {"score": 28}}}
        })
        # → {"home_score": 108, "home_team": "Lakers",
        #     "periods_1_home_score": 28}
    """

    def __init__(
        self,
        separator: str = "_",
        max_depth: int = 20,
        skip_keys: set[str] | None = None,
    ):
        self.separator = separator
        self.max_depth = max_depth
        self.skip_keys: set[str] = skip_keys or {"_comment"}

    def flatten(self, payload: dict, prefix: str = "") -> dict[str, Any]:
        """
        Return a flat dict from an arbitrarily nested *payload*.

        Args:
            payload: Raw nested dict (e.g. parsed JSON).
            prefix:  Optional string prepended to every output key — useful
                     when merging multiple sources into one flat namespace.
        """
        result: dict[str, Any] = {}
        self._walk(payload, prefix, depth=0, result=result)
        return result

    # ------------------------------------------------------------------
    # Internal recursive walker
    # ------------------------------------------------------------------

    def _walk(self, node: Any, prefix: str, depth: int, result: dict) -> None:
        """
        Core recursive descent.

        Each call carries *prefix* — the accumulated parent-key path.
        When a leaf (non-dict, non-list) is reached, it is stored under
        the current prefix.  This is the state the procedural version
        had to manage manually.
        """
        if depth > self.max_depth:
            # Store the raw subtree rather than silently dropping it.
            result[prefix] = node
            return

        if isinstance(node, dict):
            for key, value in node.items():
                if key in self.skip_keys:
                    continue
                child_key = (
                    f"{prefix}{self.separator}{key}" if prefix else key
                )
                self._walk(value, child_key, depth + 1, result)

        elif isinstance(node, list):
            for i, item in enumerate(node):
                child_key = (
                    f"{prefix}{self.separator}{i}" if prefix else str(i)
                )
                self._walk(item, child_key, depth + 1, result)

        else:
            result[prefix] = node


# ──────────────────────────────────────────────────────────────────────────────
# SchemaResolver
# ──────────────────────────────────────────────────────────────────────────────


class SchemaResolver:
    """
    Maps raw composite keys to stable canonical names.

    Used as a second pass after JsonFlattener to normalize keys that shift
    between API versions.  Keys not present in *rules* pass through unchanged,
    so you only need to list keys that actually change.

    Args:
        rules: Dict of ``{raw_key: canonical_key}``.  Load from a YAML/JSON
               config file in production so rules can evolve without code
               changes.

    Example::

        resolver = SchemaResolver({
            "league_region":    "region_name",   # v1 shape
            "region_name":      "region_name",   # v2 shape (pass-through)
            "region_league_name": "league_name", # v2 → canonical
        })
        canonical = resolver.resolve(flat_dict)
    """

    def __init__(self, rules: dict[str, str] | None = None):
        self.rules: dict[str, str] = rules or {}

    def resolve(self, flat: dict[str, Any]) -> dict[str, Any]:
        """Return a new dict with keys renamed according to *rules*."""
        return {self.rules.get(k, k): v for k, v in flat.items()}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SchemaResolver":
        """
        Load resolver rules from a YAML file.

        This lets resolver rules live in config rather than code — useful when
        a data team (not engineers) maintains the field-name mappings.
        """
        import yaml

        with open(path) as f:
            rules = yaml.safe_load(f) or {}
        return cls(rules)


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: build a FlatRecord from a raw payload in one call
# ──────────────────────────────────────────────────────────────────────────────


def flatten_to_record(
    payload: dict,
    record_id: str,
    source: str,
    resolver: SchemaResolver | None = None,
    flattener: JsonFlattener | None = None,
) -> FlatRecord:
    """
    Flatten *payload* and optionally resolve keys, returning a FlatRecord.

    This is the single entry-point for the sports pipeline — the equivalent
    of ``adapter.to_trial_record()`` in the clinical-trial pipeline, but for
    dynamic structures.

    Args:
        payload:   Raw nested JSON dict.
        record_id: Unique identifier for this record (e.g. game_id).
        source:    Logical name for the data source (e.g. ``"nba_games"``).
        resolver:  Optional SchemaResolver for key normalization.
        flattener: Optional pre-configured JsonFlattener (default settings
                   are used if omitted).
    """
    fl = flattener or JsonFlattener()
    flat = fl.flatten(payload)
    if resolver:
        flat = resolver.resolve(flat)
    return FlatRecord(record_id=record_id, source=source, fields=flat)
