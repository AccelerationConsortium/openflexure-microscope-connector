import pytest
from unitelabs.cdk import sila

from unitelabs.openflexure_microscope.features._progress import OperationPhase
from unitelabs.openflexure_microscope.features.image_acquisition_controller import ImageAcquisitionController
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_BASE_URL, SIMULATED_JPEG, SimulatedBackend

from .conftest import make_api


@pytest.fixture
def feature(api) -> ImageAcquisitionController:
    return ImageAcquisitionController(api)


def test_feature_metadata(feature):
    assert feature.identifier == "ImageAcquisitionController"
    assert feature.originator == "ca.accelerationconsortium"
    assert feature.category == "microscopy"
    assert feature.attach() is True
    assert set(feature.commands) == {"GrabPreviewFrame", "AcquireImage", "CaptureToStorage"}
    assert set(feature.properties) == {"MJPEGStreamURL"}
    assert feature.commands["AcquireImage"].observable is True
    assert feature.commands["CaptureToStorage"].observable is True
    assert feature.commands["AcquireImage"].execution_mode == sila.ExecutionMode.SINGLE
    assert feature.commands["GrabPreviewFrame"].observable is False


async def test_grab_preview_frame_returns_jpeg(feature):
    assert await feature.grab_preview_frame() == SIMULATED_JPEG


async def test_acquire_image_full_and_preview(feature, backend, status, intermediate):
    image = await feature.acquire_image(types.CaptureResolution.FULL, status=status, intermediate=intermediate)
    assert image == SIMULATED_JPEG
    assert backend.requests[-1][2] == {"use_video_port": False, "bayer": False}
    phases = [c.args[0].phase for c in intermediate.send.call_args_list]
    assert phases == [OperationPhase.STARTING, OperationPhase.COMPLETED]

    await feature.acquire_image(types.CaptureResolution.PREVIEW, status=status, intermediate=intermediate)
    assert backend.requests[-1][2] == {"use_video_port": True, "bayer": False}


async def test_capture_to_storage(feature, api, status, intermediate):
    info = await feature.capture_to_storage(
        "sample",
        ["run-1"],
        [types.Annotation("sample", "S-1"), types.Annotation("step", "3")],
        types.CaptureResolution.PREVIEW,
        status=status,
        intermediate=intermediate,
    )
    assert info.annotations == [types.Annotation("sample", "S-1"), types.Annotation("step", "3")]
    assert isinstance(info, types.CaptureInfo)
    assert info.name == "sample.jpeg"
    assert info.tags == ["run-1"]
    assert [c.id for c in await api.list_captures()] == [info.id]


async def test_mjpeg_stream_url(feature):
    assert await feature.get_mjpeg_stream_url() == f"{SIMULATED_BASE_URL}/streams/mjpeg"


async def test_missing_camera_raises_defined_error(status, intermediate):
    feature = ImageAcquisitionController(make_api(SimulatedBackend(camera_type="MissingCamera")))
    with pytest.raises(errors.CameraUnavailableError):
        await feature.grab_preview_frame()
    with pytest.raises(errors.CameraUnavailableError):
        await feature.acquire_image(types.CaptureResolution.FULL, status=status, intermediate=intermediate)
    with pytest.raises(errors.CameraUnavailableError):
        await feature.capture_to_storage(
            "x", [], [], types.CaptureResolution.FULL, status=status, intermediate=intermediate
        )


@pytest.mark.parametrize(
    ("annotations", "problem"),
    [
        ([types.Annotation("", "x")], "empty"),
        ([types.Annotation("a", "1"), types.Annotation("a", "2")], "duplicate"),
    ],
)
def test_annotation_keys_must_be_unique_and_non_empty(feature, annotations, problem):
    with pytest.raises(sila.errors.ValidationError, match=problem):
        feature._check_annotations(annotations)
    feature._check_annotations([types.Annotation("a", "1"), types.Annotation("b", "")])
