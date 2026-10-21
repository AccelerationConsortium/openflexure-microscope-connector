import inspect

import pytest
from unitelabs.cdk.config import ConfigurationError

from unitelabs.openflexure_microscope.io import api

DEVICE_CONTRACT = {
    "open",
    "aclose",
    "start_simulation",
    "start_real",
    "ping",
    "get_server_info",
    "get_position",
    "move",
    "set_zero_position",
    "get_backlash",
    "get_exposure_time_us",
    "set_exposure_time_us",
    "get_analog_gain",
    "set_analog_gain",
    "get_digital_gain",
    "set_digital_gain",
    "get_exposure_mode",
    "set_exposure_mode",
    "get_white_balance_mode",
    "set_white_balance_mode",
    "get_white_balance_gains",
    "set_white_balance_gains",
    "get_jpeg_quality",
    "set_jpeg_quality",
    "get_stream_resolution",
    "get_image_resolution",
    "grab_preview_frame",
    "acquire_image",
    "capture_to_storage",
    "list_captures",
    "download_capture",
    "read_capture_metadata",
    "set_capture_tags",
    "delete_capture",
    "fast_autofocus",
    "autofocus_sweep",
    "measure_sharpness",
    "abort_actions",
}


def test_capability_members():
    assert {c.name for c in api.Capability} == {
        "DIGITAL_GAIN",
        "EXPOSURE_MODE",
        "WHITE_BALANCE_MODE",
        "JPEG_QUALITY",
        "CAPTURE_TAGS",
        "ILLUMINATION",
    }


def test_microscope_api_is_abstract():
    with pytest.raises(TypeError):
        api.MicroscopeApi()  # type: ignore[abstract]


def test_microscope_api_declares_the_full_device_contract():
    abstract = set(api.MicroscopeApi.__abstractmethods__)
    assert abstract >= DEVICE_CONTRACT
    for name in DEVICE_CONTRACT:
        assert inspect.iscoroutinefunction(getattr(api.MicroscopeApi, name)), name
    for name in ("is_simulated", "base_url", "mjpeg_stream_url"):
        assert name in abstract
        assert isinstance(inspect.getattr_static(api.MicroscopeApi, name), property), name


def test_supports_uses_capability_set():
    class Stub(api.MicroscopeApi):
        capabilities = frozenset({api.Capability.JPEG_QUALITY})

    for name in api.MicroscopeApi.__abstractmethods__:
        setattr(Stub, name, lambda self, *a, **k: None)
    Stub.__abstractmethods__ = frozenset()
    stub = Stub()
    assert stub.supports(api.Capability.JPEG_QUALITY) is True
    assert stub.supports(api.Capability.ILLUMINATION) is False


def test_build_api_rejects_unknown_api_version():
    class Cfg:
        api_version = "v9"

    with pytest.raises(ConfigurationError):
        api.build_api(Cfg())
