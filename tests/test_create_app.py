from unittest.mock import AsyncMock

import pytest
from unitelabs.cdk import Connector, SiLAServerConfig

import unitelabs.openflexure_microscope as package
from unitelabs.openflexure_microscope import create_app
from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig
from unitelabs.openflexure_microscope.io.errors import MicroscopeUnreachableError
from unitelabs.openflexure_microscope.io.v2.client import V2Client
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

EXPECTED_FEATURES = {
    "SiLAService",
    "ConnectionConfigurationService",
    "LockController",
    "SimulationController",
    "StageController",
    "ImageAcquisitionController",
    "CaptureStorageService",
    "CameraController",
    "AutofocusController",
    "MicroscopeProvider",
}


def local_config(**overrides) -> OpenflexureMicroscopeConfig:
    return OpenflexureMicroscopeConfig(
        sila_server=SiLAServerConfig(hostname="127.0.0.1", port=0, tls=False),
        discovery=None,
        **overrides,
    )


def registered_identifiers(connector: Connector) -> set[str]:
    return {feature.identifier for feature in connector.sila_server.features.values()}


async def test_create_app_registers_all_features_in_simulation_and_closes_the_api(monkeypatch):
    api = V2Client(real_base_url="http://microscope.local:5000/api/v2", simulated=True)
    api.open = AsyncMock(wraps=api.open)
    api.aclose = AsyncMock(wraps=api.aclose)
    monkeypatch.setattr(package, "build_api", lambda config: api)

    gen = create_app(local_config())
    connector = await gen.__anext__()
    try:
        assert isinstance(connector, Connector)
        assert registered_identifiers(connector) == EXPECTED_FEATURES
        api.open.assert_not_awaited()
    finally:
        await gen.aclose()
    api.aclose.assert_awaited_once()


async def test_create_app_in_real_mode_opens_the_api_and_fails_loudly_when_unreachable(monkeypatch):
    api = V2Client(real_base_url="http://microscope.local:5000/api/v2", simulated=False)
    api.open = AsyncMock(side_effect=MicroscopeUnreachableError("no microscope"))
    api.aclose = AsyncMock()
    monkeypatch.setattr(package, "build_api", lambda config: api)

    gen = create_app(local_config(simulation_mode=False))
    with pytest.raises(MicroscopeUnreachableError):
        await gen.__anext__()
    api.aclose.assert_awaited_once()


async def test_create_app_in_real_mode_probes_the_server(monkeypatch):
    api = V2Client(
        real_base_url="http://microscope.local:5000/api/v2",
        simulated=False,
        http_backend_factory=lambda url, timeout: SimulatedBackend(base_url=url),
    )
    api.open = AsyncMock(wraps=api.open)
    monkeypatch.setattr(package, "build_api", lambda config: api)

    gen = create_app(local_config(simulation_mode=False))
    connector = await gen.__anext__()
    try:
        api.open.assert_awaited_once()
        assert registered_identifiers(connector) == EXPECTED_FEATURES
    finally:
        await gen.aclose()


async def test_create_app_uses_build_api_with_the_config():
    config = local_config()
    gen = create_app(config)
    connector = await gen.__anext__()
    try:
        feature = next(f for f in connector.sila_server.features.values() if f.identifier == "MicroscopeProvider")
        assert await feature.get_api_base_url() == "http://simulated-microscope:5000/api/v2"
    finally:
        await gen.aclose()
