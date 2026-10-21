import asyncio

import pytest
from unitelabs.cdk import sila

from unitelabs.openflexure_microscope.features._progress import OperationPhase
from unitelabs.openflexure_microscope.features.autofocus_controller import AutofocusController
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

from .conftest import make_api


@pytest.fixture
def feature(api) -> AutofocusController:
    return AutofocusController(api)


def test_feature_metadata(feature):
    assert feature.identifier == "AutofocusController"
    assert feature.attach() is True
    assert set(feature.commands) == {"RunFastAutofocus", "RunAutofocus", "MeasureSharpness", "Stop"}
    assert feature.properties == {}
    assert "TravelLimitExceededError" in feature.commands["RunFastAutofocus"].errors
    assert feature.commands["RunFastAutofocus"].observable is True
    assert feature.commands["RunAutofocus"].observable is True
    assert feature.commands["RunFastAutofocus"].execution_mode == sila.ExecutionMode.SINGLE
    assert feature.commands["RunAutofocus"].execution_mode == sila.ExecutionMode.SINGLE


async def test_fast_autofocus_returns_new_position(feature, api, status, intermediate):
    await api.move(0, 0, 500, absolute=True)
    position = await feature.run_fast_autofocus(2000, status=status, intermediate=intermediate)
    assert isinstance(position, types.StagePosition)
    assert position.z != 500
    assert position == await api.get_position()
    phases = [c.args[0].phase for c in intermediate.send.call_args_list]
    assert phases == [OperationPhase.STARTING, OperationPhase.COMPLETED]


async def test_autofocus_sweep_returns_samples_and_moves_to_sharpest(feature, api, status, intermediate):
    await api.move(0, 0, 500, absolute=True)
    samples = await feature.run_autofocus([-200, -100, 0, 100, 200], status=status, intermediate=intermediate)
    assert [s.z for s in samples] == [300, 400, 500, 600, 700]
    best = max(samples, key=lambda s: s.sharpness)
    assert (await api.get_position()).z == best.z


async def test_measure_sharpness(feature):
    assert isinstance(await feature.measure_sharpness(), float)


async def test_stop_cancels_running_autofocus(status, intermediate):
    backend = SimulatedBackend(action_duration_s=10)
    feature = AutofocusController(make_api(backend))
    running = asyncio.create_task(feature.run_fast_autofocus(2000, status=status, intermediate=intermediate))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break
    assert await feature.stop() == 1
    with pytest.raises(errors.AutofocusFailedError):
        await running


async def test_without_extension_raises_unavailable(status, intermediate):
    feature = AutofocusController(make_api(SimulatedBackend(autofocus_extension=False)))
    with pytest.raises(errors.AutofocusUnavailableError):
        await feature.run_fast_autofocus(2000, status=status, intermediate=intermediate)
    with pytest.raises(errors.AutofocusUnavailableError):
        await feature.run_autofocus([-1, 0, 1], status=status, intermediate=intermediate)
    with pytest.raises(errors.AutofocusUnavailableError):
        await feature.measure_sharpness()
