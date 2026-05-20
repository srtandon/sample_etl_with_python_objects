"""
I/O utilities: config and feed loading via pathlib, with no dependence on cwd.

All path resolution is relative to the project root, which is computed from
this file's own location so the package works regardless of where Python is
invoked from.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# src/ingestion/io.py → src/ingestion → src → project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _resolve(path: str | Path) -> Path:
    """Return an absolute Path, resolving relative paths from PROJECT_ROOT."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_yaml(path: str | Path) -> Any:
    resolved = _resolve(path)
    logger.debug(f"Loading YAML from {resolved}")
    with resolved.open("r") as f:
        return yaml.safe_load(f)


def load_json(path: str | Path) -> Any:
    resolved = _resolve(path)
    logger.debug(f"Loading JSON from {resolved}")
    with resolved.open("r") as f:
        return json.load(f)


def load_feed(path: str | Path) -> dict:
    """
    Load a raw feed dict from a YAML or JSON file.

    The feed is the raw, potentially chaotic payload that will be passed to an
    adapter before any downstream processing.
    """
    resolved = _resolve(path)
    suffix = resolved.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return load_yaml(resolved)
    if suffix == ".json":
        return load_json(resolved)
    raise ValueError(
        f"Unsupported feed format {suffix!r}. Use .yaml, .yml, or .json."
    )


def load_config(path: str | Path) -> dict:
    """Load the ingestion configuration YAML."""
    return load_yaml(path)
