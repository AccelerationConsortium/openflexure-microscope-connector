import pytest

from unitelabs.openflexure_microscope.features.camera_controller import CameraController
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.api import Capability
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

from .conftest import make_api

ALWAYS_COMMANDS = {"SetExposureTime", "SetAnalogGain", "SetWhiteBalanceGains"}
ALWAYS_PROPERTIES = {"ExposureTime", "AnalogGain", "WhiteBalanceGains", "StreamResolution", "ImageResolution"}
GATED_COMMANDS = {"SetDigitalGain", "SetExposureMode", "SetWhiteBalanceMode", "SetJPEGQuality"}
GATED_PROPERTIES = {"DigitalGain", "ExposureMode", "WhiteBalanceMode", "JPEGQuality"}


@pytest.fixture
def feature(api) -> CameraController:
    return CameraController(api)


def test_feature_metadata_with_all_capabilities(feature):
    assert feature.identifier == "CameraController"
    assert feature.attach() is True
    assert set(feature.commands) == ALWAYS_COMMANDS | GATED_COMMANDS
    assert set(feature.properties) == ALWAYS_PROPERTIES | GATED_PROPERTIES


def test_gated_endpoints_disappear_without_capabilities(api):
    api.capabilities = frozenset()
    feature = CameraController(api)
    assert feature.attach() is True
    assert set(feature.commands) == ALWAYS_COMMANDS
    assert set(feature.properties) == ALWAYS_PROPERTIES


@pytest.mark.parametrize(
    ("capability", "command", "prop"),
    [
        (Capability.DIGITAL_GAIN, "SetDigitalGain", "DigitalGain"),
        (Capability.EXPOSURE_MODE, "SetExposureMode", "ExposureMode"),
        (Capability.WHITE_BALANCE_MODE, "SetWhiteBalanceMode", "WhiteBalanceMode"),
        (Capability.JPEG_QUALITY, "SetJPEGQuality", "JPEGQuality"),
    ],
)
def test_each_gate_follows_its_capability(api, capability, command, prop):
    api.capabilities = frozenset({capability})
    feature = CameraController(api)
    feature.attach()
    assert set(feature.commands) == ALWAYS_COMMANDS | {command}
    assert set(feature.properties) == ALWAYS_PROPERTIES | {prop}


async def test_exposure_and_gains_round_trip(feature):
    assert await feature.get_exposure_time() == 20000
    await feature.set_exposure_time(12345)
    assert await feature.get_exposure_time() == 12345

    await feature.set_analog_gain(3.0)
    assert await feature.get_analog_gain() == 3.0
    await feature.set_digital_gain(1.25)
    assert await feature.get_digital_gain() == 1.25

    await feature.set_white_balance_gains(1.7, 1.3)
    assert await feature.get_white_balance_gains() == types.WhiteBalanceGains(red=1.7, blue=1.3)
    assert await feature.get_white_balance_mode() is types.WhiteBalanceMode.OFF


async def test_modes_and_quality_round_trip(feature):
    await feature.set_exposure_mode(types.ExposureMode.SPORTS)
    assert await feature.get_exposure_mode() is types.ExposureMode.SPORTS
    await feature.set_white_balance_mode(types.WhiteBalanceMode.CLOUDY)
    assert await feature.get_white_balance_mode() is types.WhiteBalanceMode.CLOUDY
    await feature.set_jpeg_quality(85)
    assert await feature.get_jpeg_quality() == 85


async def test_resolutions(feature):
    assert await feature.get_stream_resolution() == types.Resolution(832, 624)
    assert await feature.get_image_resolution() == types.Resolution(3280, 2464)


async def test_missing_camera_reports_unsupported_setting():
    feature = CameraController(make_api(SimulatedBackend(camera_type="MissingCamera")))
    with pytest.raises(errors.CameraSettingUnsupportedError):
        await feature.get_exposure_time()
    with pytest.raises(errors.CameraSettingUnsupportedError):
        await feature.set_white_balance_gains(1.0, 1.0)
    assert await feature.get_stream_resolution() == types.Resolution(832, 624)
