import asyncio

import pytest

from unitelabs.openflexure_microscope.features.microscope_provider import MicroscopeProvider
from unitelabs.openflexure_microscope.io import types
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_BASE_URL


@pytest.fixture
def feature(api) -> MicroscopeProvider:
    return MicroscopeProvider(api, reachable_poll_interval_s=0.005)


def test_feature_metadata(feature):
    assert feature.identifier == "MicroscopeProvider"
    assert feature.attach() is True
    assert feature.commands == {}
    assert set(feature.properties) == {
        "MicroscopeName",
        "MicroscopeId",
        "ServerVersion",
        "CameraType",
        "InstalledExtensions",
        "APIBaseURL",
        "Reachable",
    }
    assert feature.properties["Reachable"].observable is True


async def test_identity_properties(feature):
    assert await feature.get_microscope_name() == "Simulated OpenFlexure Microscope"
    assert (await feature.get_microscope_id()).startswith("openflexure:microscope:")
    assert await feature.get_server_version() == "2.11.0"
    assert await feature.get_camera_type() is types.CameraType.PI_CAMERA_STREAMER
    assert "org.openflexure.autofocus" in await feature.get_installed_extensions()
    assert await feature.get_api_base_url() == SIMULATED_BASE_URL


async def test_reachable_stream_reports_current_state(feature):
    stream = feature.subscribe_reachable()
    assert await asyncio.wait_for(anext(stream), 1) is True
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(stream), 0.05)
    await stream.aclose()
