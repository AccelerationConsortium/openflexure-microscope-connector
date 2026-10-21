import dataclasses
import typing

import pytest

from unitelabs.openflexure_microscope.io import types


def test_stage_position_from_mapping_coerces_to_int():
    pos = types.StagePosition.from_mapping({"x": 10.0, "y": -5, "z": "20"})
    assert pos == types.StagePosition(x=10, y=-5, z=20)


def test_stage_position_from_mapping_requires_all_axes():
    with pytest.raises(KeyError):
        types.StagePosition.from_mapping({"x": 1, "y": 2})


def test_stage_position_fields_carry_step_unit():
    hints = typing.get_type_hints(types.StagePosition, include_extras=True)
    for axis in ("x", "y", "z"):
        annotation = hints[axis]
        assert typing.get_origin(annotation) is typing.Annotated
        base, *constraints = typing.get_args(annotation)
        assert base is int
        assert any(getattr(c, "label", None) == "step" for c in constraints)


def test_stage_type_unknown_value_maps_to_unknown_member():
    assert types.StageType("SangaStage") is types.StageType.SANGA_STAGE
    assert types.StageType("SangaDeltaStage") is types.StageType.SANGA_DELTA_STAGE
    assert types.StageType("MissingStage") is types.StageType.MISSING_STAGE
    assert types.StageType("SomethingNew") is types.StageType.UNKNOWN


def test_camera_type_unknown_value_maps_to_unknown_member():
    assert types.CameraType("PiCameraStreamer") is types.CameraType.PI_CAMERA_STREAMER
    assert types.CameraType("MissingCamera") is types.CameraType.MISSING_CAMERA
    assert types.CameraType("Webcam") is types.CameraType.UNKNOWN


def test_exposure_mode_values_are_picamera_strings():
    assert types.ExposureMode("off") is types.ExposureMode.OFF
    assert types.ExposureMode("nightpreview") is types.ExposureMode.NIGHT_PREVIEW
    assert {m.value for m in types.ExposureMode} == {
        "off",
        "auto",
        "night",
        "nightpreview",
        "backlight",
        "spotlight",
        "sports",
        "snow",
        "beach",
        "verylong",
        "fixedfps",
        "antishake",
        "fireworks",
    }


def test_white_balance_mode_values_are_picamera_strings():
    assert {m.value for m in types.WhiteBalanceMode} == {
        "off",
        "auto",
        "sunlight",
        "cloudy",
        "shade",
        "tungsten",
        "fluorescent",
        "incandescent",
        "flash",
        "horizon",
    }


def test_capture_resolution_maps_to_video_port_flag():
    assert types.CaptureResolution.PREVIEW.use_video_port is True
    assert types.CaptureResolution.FULL.use_video_port is False


def test_capture_info_from_v2_capture_dict():
    raw = {
        "id": "abc-123",
        "name": "img.jpeg",
        "path": "/var/openflexure/data/micrographs/img.jpeg",
        "time": "2026-10-20T12:00:00",
        "format": "jpeg",
        "tags": ["docs"],
        "annotations": {"Client": "SwaggerUI"},
        "links": {"self": {"href": "http://x"}},
    }
    info = types.CaptureInfo.from_mapping(raw)
    assert info == types.CaptureInfo(
        id="abc-123",
        name="img.jpeg",
        path="/var/openflexure/data/micrographs/img.jpeg",
        time="2026-10-20T12:00:00",
        format="jpeg",
        tags=["docs"],
    )


def test_capture_info_tolerates_missing_optional_fields():
    info = types.CaptureInfo.from_mapping({"id": "1", "name": "a.jpeg"})
    assert info.path == ""
    assert info.time == ""
    assert info.format == ""
    assert info.tags == []


def test_dataclasses_are_plain_dataclasses_for_sila_structures():
    for cls in (
        types.StagePosition,
        types.Backlash,
        types.Resolution,
        types.WhiteBalanceGains,
        types.CaptureInfo,
        types.SharpnessSample,
        types.ServerInfo,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__doc__ and "Attributes:" in cls.__doc__
