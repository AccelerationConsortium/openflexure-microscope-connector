import asyncio
from unittest.mock import call

import pytest
from unitelabs.cdk import sila

from unitelabs.openflexure_microscope.features._progress import OperationPhase
from unitelabs.openflexure_microscope.features.stage_controller import StageController
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

from .conftest import make_api


@pytest.fixture
def feature(api) -> StageController:
    return StageController(api, position_poll_interval_s=0.005)


def test_feature_metadata(feature):
    assert feature.identifier == "StageController"
    assert feature.originator == "ca.accelerationconsortium"
    assert feature.category == "microscopy"
    assert feature.attach() is True
    assert set(feature.commands) == {"MoveTo", "MoveBy", "SetZeroPosition", "Stop"}
    assert set(feature.properties) == {"Position", "LivePosition", "StageType", "Backlash"}
    assert feature.commands["MoveTo"].observable is True
    assert feature.commands["MoveBy"].observable is True
    assert feature.commands["MoveTo"].execution_mode == sila.ExecutionMode.SINGLE
    assert feature.commands["MoveBy"].execution_mode == sila.ExecutionMode.SINGLE
    assert feature.properties["LivePosition"].observable is True


async def test_position_and_backlash(feature):
    assert await feature.get_position() == types.StagePosition(0, 0, 0)
    assert await feature.get_backlash() == types.Backlash(100, 100, 100)
    assert await feature.get_stage_type() is types.StageType.SANGA_STAGE


async def test_move_to_and_move_by_report_progress(feature, status, intermediate):
    result = await feature.move_to(100, 50, -10, status=status, intermediate=intermediate)
    assert result == types.StagePosition(100, 50, -10)
    phases = [c.args[0].phase for c in intermediate.send.call_args_list]
    assert phases == [OperationPhase.STARTING, OperationPhase.COMPLETED]
    assert status.update.call_args_list[0] == call(progress=0.0)
    assert status.update.call_args_list[-1] == call(progress=1.0)

    result = await feature.move_by(-100, 0, 10, status=status, intermediate=intermediate)
    assert result == types.StagePosition(0, 50, 0)
    assert await feature.get_position() == types.StagePosition(0, 50, 0)


async def test_set_zero_position(feature, status, intermediate):
    await feature.move_to(7, 8, 9, status=status, intermediate=intermediate)
    assert await feature.set_zero_position() == types.StagePosition(0, 0, 0)
    assert await feature.get_position() == types.StagePosition(0, 0, 0)


async def test_live_position_yields_on_change_only(feature, status, intermediate):
    stream = feature.subscribe_position()
    first = await asyncio.wait_for(anext(stream), 1)
    assert first == types.StagePosition(0, 0, 0)

    await feature.move_by(5, 0, 0, status=status, intermediate=intermediate)
    second = await asyncio.wait_for(anext(stream), 1)
    assert second == types.StagePosition(5, 0, 0)

    # No change: the stream must not produce another value promptly.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(stream), 0.05)
    await stream.aclose()


async def test_stop_cancels_running_move(status, intermediate):
    backend = SimulatedBackend(action_duration_s=10)
    feature = StageController(make_api(backend))
    moving = asyncio.create_task(feature.move_by(0, 0, 1000, status=status, intermediate=intermediate))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break

    assert await feature.stop() == 1
    with pytest.raises(errors.MoveFailedError):
        await moving
    assert await feature.get_position() == types.StagePosition(0, 0, 0)
    assert await feature.stop() == 0


async def test_missing_stage_errors_surface_as_defined_errors(status, intermediate):
    feature = StageController(make_api(SimulatedBackend(stage_type="MissingStage")))
    assert await feature.get_stage_type() is types.StageType.MISSING_STAGE
    with pytest.raises(errors.StageUnavailableError):
        await feature.move_to(1, 1, 1, status=status, intermediate=intermediate)
    with pytest.raises(errors.StageUnavailableError):
        await feature.set_zero_position()
    phases = [c.args[0].phase for c in intermediate.send.call_args_list]
    assert phases == [OperationPhase.STARTING]


def test_declared_errors_cover_the_io_errors(feature):
    feature.attach()
    move_errors = {e.identifier for e in feature.commands["MoveTo"].errors.values()}
    assert move_errors == {"StageUnavailableError", "StageBusyError", "MoveFailedError", "MicroscopeUnreachableError"}
    zero_errors = {e.identifier for e in feature.commands["SetZeroPosition"].errors.values()}
    assert zero_errors == {"StageUnavailableError", "StageBusyError", "MoveFailedError", "MicroscopeUnreachableError"}
