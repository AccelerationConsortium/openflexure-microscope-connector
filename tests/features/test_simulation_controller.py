import asyncio

import httpx
import pytest
from unitelabs.cdk.features.core.simulation_controller import StartRealModeFailed

from unitelabs.openflexure_microscope.features.simulation_controller import SimulationController
from unitelabs.openflexure_microscope.io.v2.backend import HttpBackend
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

from .conftest import REAL_URL, make_api


@pytest.fixture
def feature(api) -> SimulationController:
    return SimulationController(api)


def test_feature_metadata(feature):
    assert feature.identifier == "SimulationController"
    assert feature.originator == "org.silastandard"
    assert feature.category == "core"
    assert feature.attach() is True
    assert set(feature.commands) == {"StartSimulationMode", "StartRealMode"}
    assert set(feature.properties) == {"SimulationMode"}


async def test_starts_in_simulation_and_real_mode_failure_keeps_it(feature, api):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api._http_backend_factory = lambda url, timeout: HttpBackend(
        url, timeout_s=timeout, transport=httpx.MockTransport(refused)
    )
    assert await feature.simulation_mode() is True
    with pytest.raises(StartRealModeFailed):
        await feature.start_real_mode()
    assert await feature.simulation_mode() is True


async def test_switching_to_real_and_back(feature, api):
    api._http_backend_factory = lambda url, timeout: SimulatedBackend(base_url=REAL_URL)
    await feature.start_real_mode()
    assert await feature.simulation_mode() is False
    assert api.base_url == REAL_URL
    await feature.start_simulation_mode()
    assert await feature.simulation_mode() is True


async def test_switching_while_an_action_runs_fails_as_start_real_mode_failed():
    backend = SimulatedBackend(action_duration_s=10)
    api = make_api(backend)
    api._http_backend_factory = lambda url, timeout: SimulatedBackend(base_url=REAL_URL)
    feature = SimulationController(api)
    moving = asyncio.create_task(api.move(0, 0, 1000, absolute=False))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break
    with pytest.raises(StartRealModeFailed):
        await feature.start_real_mode()
    assert await feature.simulation_mode() is True
    moving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await moving
