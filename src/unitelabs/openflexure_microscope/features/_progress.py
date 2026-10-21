"""
Shared observable-command progress reporting.

Adapted from the Acceleration Consortium's opentrons-flex connector: every long-running SiLA command reports a
`STARTING` and a `COMPLETED` (or `CANCELLED`) intermediate response and forwards the server's real progress to the
execution status. No progress is invented: between start and end only values reported by the microscope are relayed.
"""

import asyncio
import collections.abc
import dataclasses
import enum
import typing

from unitelabs.cdk import sila

from ..io.api import ProgressCallback

T = typing.TypeVar("T")


class OperationPhase(enum.Enum):
    """Lifecycle phase of a long-running microscope operation."""

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclasses.dataclass
class OperationProgress:
    """
    Intermediate progress update for a long-running microscope operation.

    Attributes:
      Phase: Lifecycle phase of the operation.
      Message: Human-readable description of the current step.
    """

    phase: OperationPhase
    message: str


def report_progress(
    status: sila.Status,
    intermediate: sila.Intermediate[OperationProgress],
    progress: float,
    phase: OperationPhase,
    message: str,
) -> None:
    """Send a status update and a matching intermediate response."""
    status.update(progress=progress)
    intermediate.send(OperationProgress(phase=phase, message=message))


async def run_observable(
    status: sila.Status,
    intermediate: sila.Intermediate[OperationProgress],
    *,
    start: str,
    complete: str,
    cancelled: str,
    action: collections.abc.Callable[[ProgressCallback], collections.abc.Awaitable[T]],
) -> T:
    """
    Run `action(on_progress)` with standard progress reporting.

    `STARTING` is reported at progress 0, each strictly increasing fraction the device reports is forwarded to the
    status, and `COMPLETED` is reported at progress 1. If the command is cancelled, `CANCELLED` is reported and the
    cancellation propagates so the device layer can abort the server-side action.
    """
    report_progress(status, intermediate, 0.0, OperationPhase.STARTING, start)
    highest = 0.0

    def on_progress(fraction: float | None) -> None:
        nonlocal highest
        if fraction is None or fraction <= highest:
            return
        highest = min(fraction, 1.0)
        status.update(progress=highest)

    try:
        result = await action(on_progress)
    except asyncio.CancelledError:
        report_progress(status, intermediate, 1.0, OperationPhase.CANCELLED, cancelled)
        raise
    report_progress(status, intermediate, 1.0, OperationPhase.COMPLETED, complete)
    return result
