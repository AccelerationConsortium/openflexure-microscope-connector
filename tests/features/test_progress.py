import asyncio
import dataclasses
from unittest.mock import Mock, call

import pytest

from unitelabs.openflexure_microscope.features._progress import OperationPhase, OperationProgress, run_observable


def sent_phases(intermediate: Mock) -> list[tuple[OperationPhase, str]]:
    return [(c.args[0].phase, c.args[0].message) for c in intermediate.send.call_args_list]


async def test_run_observable_reports_start_and_completion_and_returns_result(status, intermediate):
    async def action(on_progress):
        return "done"

    result = await run_observable(
        status,
        intermediate,
        start="Moving the stage.",
        complete="Stage move finished.",
        cancelled="Stage move cancelled.",
        action=action,
    )

    assert result == "done"
    assert sent_phases(intermediate) == [
        (OperationPhase.STARTING, "Moving the stage."),
        (OperationPhase.COMPLETED, "Stage move finished."),
    ]
    assert status.update.call_args_list[0] == call(progress=0.0)
    assert status.update.call_args_list[-1] == call(progress=1.0)


async def test_run_observable_forwards_increasing_server_progress_only(status, intermediate):
    async def action(on_progress):
        on_progress(None)
        on_progress(0.2)
        on_progress(0.1)
        on_progress(0.2)
        on_progress(0.7)
        return

    await run_observable(status, intermediate, start="s", complete="c", cancelled="x", action=action)

    progresses = [c.kwargs["progress"] for c in status.update.call_args_list]
    assert progresses == [0.0, 0.2, 0.7, 1.0]
    assert [phase for phase, _ in sent_phases(intermediate)] == [OperationPhase.STARTING, OperationPhase.COMPLETED]


async def test_run_observable_reports_cancellation_and_reraises(status, intermediate):
    async def action(on_progress):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_observable(status, intermediate, start="s", complete="c", cancelled="Move cancelled.", action=action)

    assert sent_phases(intermediate)[-1] == (OperationPhase.CANCELLED, "Move cancelled.")


async def test_run_observable_lets_other_errors_propagate_without_completion(status, intermediate):
    async def action(on_progress):
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        await run_observable(status, intermediate, start="s", complete="c", cancelled="x", action=action)

    assert [phase for phase, _ in sent_phases(intermediate)] == [OperationPhase.STARTING]


def test_operation_progress_is_a_sila_structure():
    assert dataclasses.is_dataclass(OperationProgress)
    assert "Attributes:" in OperationProgress.__doc__
    assert {m.name for m in OperationPhase} == {"STARTING", "RUNNING", "COMPLETED", "CANCELLED"}
