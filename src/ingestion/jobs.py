"""
ImportJob: composes a TrialRecord with a list of processors and runs them.

This replaces the create_import() dynamic-subclass pattern.  Composition is
preferable here because:

  - The trial object and the job logic are decoupled — swapping processors
    does not require a new subclass or MRO reasoning.
  - A single TrialRecord instance is shared across all processors (no double
    construction of TrialB, etc.).
  - PipelineResult gives callers a structured success/failure report rather
    than relying on log scanning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ingestion.models import TrialRecord

logger = logging.getLogger(__name__)


@runtime_checkable
class Processor(Protocol):
    """
    Structural protocol for processors.

    Any object with a ``run(trial: TrialRecord)`` method satisfies this
    interface — no base class inheritance required.
    """

    def run(self, trial: TrialRecord) -> object:
        ...


@dataclass
class PipelineResult:
    """Structured outcome of a single ImportJob run."""

    trial_id: str
    success: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, object] = field(default_factory=dict)

    def add_error(self, message: str) -> None:
        self.success = False
        self.errors.append(message)

    def summary(self) -> str:
        status = "OK" if self.success else "FAILED"
        parts = [f"[{status}] {self.trial_id}"]
        for name, value in self.data.items():
            parts.append(f"  {name}: {value}")
        if self.errors:
            parts.append(f"  errors: {self.errors}")
        return "\n".join(parts)


class ImportJob:
    """
    Runs a sequence of processors against a single TrialRecord.

    Usage::

        job = ImportJob(
            trial=adapter.to_trial_record("TrialB", raw_payload),
            processors=[CohortProcessor(["A1", "A4"]), ScheduleProcessor()],
        )
        result = job.run()
        print(result.summary())
    """

    def __init__(self, trial: TrialRecord, processors: list[Processor]):
        self.trial = trial
        self.processors = processors

    def run(self) -> PipelineResult:
        result = PipelineResult(trial_id=self.trial.trial_id)
        logger.info(f"Starting import for {self.trial.trial_id!r}")

        for processor in self.processors:
            name = type(processor).__name__
            try:
                output = processor.run(self.trial)
                result.data[name] = output
                logger.info(f"{name} completed.")
            except Exception as exc:
                result.add_error(f"{name}: {exc}")
                logger.error(f"{name} failed: {exc}", exc_info=True)

        logger.info(f"Import finished — success={result.success}")
        return result
