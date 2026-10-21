"""gRPC round trips through a real local SiLA server with the `unitelabs-sila` client (simulation mode)."""

import contextlib
import importlib.util
import pathlib

import pytest
from sila.client import Client, ClientConfig
from unitelabs.cdk import SiLAServerConfig

import unitelabs.openflexure_microscope as package
from unitelabs.openflexure_microscope import create_app
from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig
from unitelabs.openflexure_microscope.io.v2.client import V2Client
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_JPEG, SimulatedBackend

FQ = "ca.accelerationconsortium/microscopy/{}/v1"

_SMOKE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "smoke_client.py"
_spec = importlib.util.spec_from_file_location("smoke_client", _SMOKE)
smoke_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_client)
wait_for_responses = smoke_client.wait_for_responses


@pytest.fixture
async def connector():
    config = OpenflexureMicroscopeConfig(
        sila_server=SiLAServerConfig(hostname="127.0.0.1", port=0, tls=False),
        discovery=None,
    )
    config.cloud_servers.enabled = False
    gen = create_app(config)
    connector = await gen.__anext__()
    await connector.start()
    try:
        yield connector
    finally:
        await connector.stop()
        with contextlib.suppress(StopAsyncIteration):
            await gen.aclose()


@pytest.fixture
async def client(connector):
    port = int(connector.sila_server._address.rpartition(":")[2])
    client = Client(ClientConfig(hostname="127.0.0.1", port=port, tls=False, default_timeout=10_000))
    async with client:
        yield client


async def test_server_lists_every_feature(client):
    sila_service = await client.load_feature("org.silastandard/core/SiLAService/v1")
    implemented = await sila_service.properties["ImplementedFeatures"].read()
    assert FQ.format("StageController") in implemented
    assert FQ.format("ImageAcquisitionController") in implemented
    assert FQ.format("CaptureStorageService") in implemented
    assert FQ.format("CameraController") in implemented
    assert FQ.format("AutofocusController") in implemented
    assert FQ.format("MicroscopeProvider") in implemented
    assert "org.silastandard/core/SimulationController/v1" in implemented
    assert "org.silastandard/core/LockController/v2" in implemented


async def test_stage_move_round_trip(client):
    stage = await client.load_feature(FQ.format("StageController"))
    assert await stage.properties["Position"].read() == {"X": 0, "Y": 0, "Z": 0}

    execution = await stage.commands["MoveBy"].execute({"X": 10, "Y": -5, "Z": 2})
    responses = await wait_for_responses(execution)
    assert responses == {"Position": {"X": 10, "Y": -5, "Z": 2}}
    assert await stage.properties["Position"].read() == {"X": 10, "Y": -5, "Z": 2}

    execution = await stage.commands["MoveTo"].execute({"X": 0, "Y": 0, "Z": 0})
    assert (await wait_for_responses(execution))["Position"] == {"X": 0, "Y": 0, "Z": 0}


async def test_binary_image_round_trip(client):
    acquisition = await client.load_feature(FQ.format("ImageAcquisitionController"))
    result = await acquisition.commands["GrabPreviewFrame"].execute({})
    assert result["Image"] == SIMULATED_JPEG

    execution = await acquisition.commands["AcquireImage"].execute({"Resolution": "preview"})
    assert (await wait_for_responses(execution))["Image"] == SIMULATED_JPEG


async def test_enum_settings_round_trip(client):
    camera = await client.load_feature(FQ.format("CameraController"))
    assert await camera.properties["ExposureTime"].read() == 20000
    await camera.commands["SetExposureTime"].execute({"ExposureTime": 4000})
    assert await camera.properties["ExposureTime"].read() == 4000

    # Enum members travel as their lowercased names with spaces (CDK rule), e.g. NIGHT_PREVIEW -> "night preview".
    await camera.commands["SetExposureMode"].execute({"Mode": "night preview"})
    assert await camera.properties["ExposureMode"].read() == "night preview"


async def test_defined_execution_error_round_trip(client):
    storage = await client.load_feature(FQ.format("CaptureStorageService"))
    with pytest.raises(Exception, match=r"CaptureNotFound|No capture") as exc_info:
        await storage.commands["DownloadCapture"].execute({"CaptureId": "does-not-exist"})
    assert "CaptureNotFound" in type(exc_info.value).__name__ or "CaptureNotFound" in str(exc_info.value)


async def test_simulation_mode_is_on(client):
    simulation = await client.load_feature("org.silastandard/core/SimulationController/v1")
    assert await simulation.properties["SimulationMode"].read() is True


@pytest.mark.parametrize(
    ("identifier", "endpoint"),
    [
        ("StageController", "MoveTo"),
        ("ImageAcquisitionController", "AcquireImage"),
        ("CaptureStorageService", "DownloadCapture"),
        ("CameraController", "SetExposureTime"),
        ("AutofocusController", "RunFastAutofocus"),
        ("MicroscopeProvider", "ServerVersion"),
    ],
)
async def test_feature_definitions_are_served_as_sila_xml(client, identifier, endpoint):
    sila_service = await client.load_feature("org.silastandard/core/SiLAService/v1")
    result = await sila_service.commands["GetFeatureDefinition"].execute({"FeatureIdentifier": FQ.format(identifier)})
    xml = result["FeatureDefinition"]
    assert f"<Identifier>{identifier}</Identifier>" in xml
    assert f"<Identifier>{endpoint}</Identifier>" in xml
    assert 'Originator="ca.accelerationconsortium"' in xml
    assert 'Category="microscopy"' in xml


@pytest.fixture
async def slow_client(monkeypatch):
    """A connector whose simulated server needs 0.3 s per action, like real hardware."""
    api = V2Client(
        real_base_url="http://microscope.local:5000/api/v2",
        simulated=True,
        simulated_backend=SimulatedBackend(action_duration_s=0.3),
        action_poll_interval_s=0.02,
    )
    monkeypatch.setattr(package, "build_api", lambda config: api)
    config = OpenflexureMicroscopeConfig(
        sila_server=SiLAServerConfig(hostname="127.0.0.1", port=0, tls=False),
        discovery=None,
    )
    config.cloud_servers.enabled = False
    gen = create_app(config)
    connector = await gen.__anext__()
    await connector.start()
    port = int(connector.sila_server._address.rpartition(":")[2])
    try:
        async with Client(ClientConfig(hostname="127.0.0.1", port=port, tls=False, default_timeout=10_000)) as client:
            yield client
    finally:
        await connector.stop()
        with contextlib.suppress(StopAsyncIteration):
            await gen.aclose()


async def test_slow_observable_command_result_is_read_after_completion(slow_client):
    stage = await slow_client.load_feature(FQ.format("StageController"))
    execution = await stage.commands["MoveBy"].execute({"X": 0, "Y": 0, "Z": 7})
    responses = await wait_for_responses(execution)
    assert responses == {"Position": {"X": 0, "Y": 0, "Z": 7}}
    assert execution.done is True
