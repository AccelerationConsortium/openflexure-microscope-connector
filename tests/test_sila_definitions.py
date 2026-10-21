"""The SiLA feature definitions of every feature must build and serve on a real (local) SiLA server."""

import contextlib

import pytest
from unitelabs.cdk import SiLAServerConfig

from unitelabs.openflexure_microscope import create_app
from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig

DEVICE_FEATURES = {
    "StageController": (
        {"MoveTo", "MoveBy", "SetZeroPosition", "Stop"},
        {"Position", "LivePosition", "StageType", "Backlash", "TravelLimits"},
    ),
    "ImageAcquisitionController": ({"GrabPreviewFrame", "AcquireImage", "CaptureToStorage"}, {"MJPEGStreamURL"}),
    "CaptureStorageService": (
        {
            "DownloadCapture",
            "DownloadThumbnail",
            "ReadCaptureMetadata",
            "SetCaptureTags",
            "DeleteCapture",
            "ExportCapture",
        },
        {"Captures", "ExportFolder"},
    ),
    "CameraController": (
        {
            "SetExposureTime",
            "SetAnalogGain",
            "SetWhiteBalanceGains",
            "SetDigitalGain",
            "SetExposureMode",
            "SetWhiteBalanceMode",
            "SetJPEGQuality",
        },
        {
            "ExposureTime",
            "AnalogGain",
            "WhiteBalanceGains",
            "StreamResolution",
            "ImageResolution",
            "DigitalGain",
            "ExposureMode",
            "WhiteBalanceMode",
            "JPEGQuality",
        },
    ),
    "AutofocusController": ({"RunFastAutofocus", "RunAutofocus", "MeasureSharpness", "Stop"}, set()),
    "MicroscopeProvider": (
        set(),
        {
            "MicroscopeName",
            "MicroscopeId",
            "ServerVersion",
            "CameraType",
            "InstalledExtensions",
            "APIBaseURL",
            "Reachable",
        },
    ),
    "SimulationController": ({"StartSimulationMode", "StartRealMode"}, {"SimulationMode"}),
}


@pytest.fixture
async def started_connector():
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


async def test_every_feature_definition_builds_and_exposes_the_planned_endpoints(started_connector):
    features = {feature.identifier: feature for feature in started_connector.sila_server.features.values()}
    for identifier, (commands, properties) in DEVICE_FEATURES.items():
        feature = features[identifier]
        assert set(feature.commands) == commands, identifier
        assert set(feature.properties) == properties, identifier
        assert feature.fully_qualified_identifier.startswith(
            "org.silastandard/core/"
            if identifier == "SimulationController"
            else "ca.accelerationconsortium/microscopy/"
        )
