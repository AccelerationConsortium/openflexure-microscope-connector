import asyncio
import json
import re

import httpx
import pytest

from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.api import Capability, MicroscopeApi, build_api
from unitelabs.openflexure_microscope.io.v2.backend import HttpBackend
from unitelabs.openflexure_microscope.io.v2.client import V2Client
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_BASE_URL, SIMULATED_JPEG, SimulatedBackend

REAL_URL = "http://microscope.local:5000/api/v2"


def make_client(backend: SimulatedBackend | None = None, **kwargs) -> V2Client:
    return V2Client(
        real_base_url=REAL_URL,
        simulated=True,
        simulated_backend=backend or SimulatedBackend(),
        action_poll_interval_s=0.005,
        **kwargs,
    )


@pytest.fixture
def backend() -> SimulatedBackend:
    return SimulatedBackend()


@pytest.fixture
def client(backend) -> V2Client:
    return make_client(backend)


# -- construction / lifecycle -------------------------------------------------------------------------------------


def test_build_api_from_config_returns_simulated_v2_client():
    config = OpenflexureMicroscopeConfig(host="scope.lab", port=5001)
    client = build_api(config)
    assert isinstance(client, V2Client)
    assert isinstance(client, MicroscopeApi)
    assert client.is_simulated is True
    assert client.base_url == SIMULATED_BASE_URL
    assert client.mjpeg_stream_url == f"{SIMULATED_BASE_URL}/streams/mjpeg"
    assert client.real_base_url == "http://scope.lab:5001/api/v2"
    assert client.capabilities == frozenset(
        {
            Capability.DIGITAL_GAIN,
            Capability.EXPOSURE_MODE,
            Capability.WHITE_BALANCE_MODE,
            Capability.JPEG_QUALITY,
            Capability.CAPTURE_TAGS,
        }
    )
    assert client.supports(Capability.ILLUMINATION) is False


async def test_open_in_simulation_does_not_touch_the_network(client, backend):
    await client.open()
    assert len(backend.requests) == 0
    assert await client.ping() is True
    await client.aclose()


async def test_open_in_real_mode_rejects_a_server_without_api_v2():
    def v3_like(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    client = V2Client(
        real_base_url=REAL_URL,
        simulated=False,
        http_backend_factory=lambda url, timeout: HttpBackend(
            url, timeout_s=timeout, transport=httpx.MockTransport(v3_like)
        ),
    )
    with pytest.raises(errors.MicroscopeUnreachableError, match=re.escape("/api/v2")):
        await client.open()
    await client.aclose()


async def test_open_in_real_mode_requires_a_reachable_server():
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = V2Client(
        real_base_url=REAL_URL,
        simulated=False,
        http_backend_factory=lambda url, timeout: HttpBackend(
            url, timeout_s=timeout, transport=httpx.MockTransport(refused)
        ),
    )
    with pytest.raises(errors.MicroscopeUnreachableError):
        await client.open()
    assert await client.ping() is False
    await client.aclose()


async def test_start_real_failure_keeps_simulation(client):
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client._http_backend_factory = lambda url, timeout: HttpBackend(
        url, timeout_s=timeout, transport=httpx.MockTransport(refused)
    )
    with pytest.raises(errors.MicroscopeUnreachableError):
        await client.start_real()
    assert client.is_simulated is True
    assert await client.get_position() == types.StagePosition(0, 0, 0)


async def test_start_real_and_back_to_simulation_swap_backends(client):
    real = SimulatedBackend(base_url=REAL_URL)
    await real.request("POST", "/actions/stage/move/", json={"x": 42, "absolute": True})
    client._http_backend_factory = lambda url, timeout: real

    await client.start_real()
    assert client.is_simulated is False
    assert client.base_url == REAL_URL
    assert (await client.get_position()).x == 42

    await client.start_simulation()
    assert client.is_simulated is True
    assert client.base_url == SIMULATED_BASE_URL
    assert (await client.get_position()).x == 0


async def test_server_info(client):
    info = await client.get_server_info()
    assert info.version == "2.11.0"
    assert info.camera_type is types.CameraType.PI_CAMERA_STREAMER
    assert info.stage_type is types.StageType.SANGA_STAGE
    assert info.name == "Simulated OpenFlexure Microscope"
    assert info.id.startswith("openflexure:microscope:")
    assert "org.openflexure.autofocus" in info.extensions


# -- stage --------------------------------------------------------------------------------------------------------


async def test_moves_and_zero(client):
    assert await client.get_position() == types.StagePosition(0, 0, 0)
    assert await client.move(100, -20, 5, absolute=False) == types.StagePosition(100, -20, 5)
    assert await client.move(1, 2, 3, absolute=True) == types.StagePosition(1, 2, 3)
    assert await client.set_zero_position() == types.StagePosition(0, 0, 0)
    assert await client.get_backlash() == types.Backlash(100, 100, 100)


async def test_move_reports_progress_while_polling():
    backend = SimulatedBackend(action_duration_s=0.05)
    client = make_client(backend)
    seen: list[float | None] = []
    result = await client.move(0, 0, 10, absolute=False, on_progress=seen.append)
    assert result == types.StagePosition(0, 0, 10)
    assert seen[-1] == 1.0
    fractions = [f for f in seen if f is not None]
    assert fractions == sorted(fractions)
    assert any(method == "GET" and "/actions/" in path for method, path, *_ in backend.requests)


async def test_cancelling_a_move_cancels_the_server_action():
    backend = SimulatedBackend(action_duration_s=10)
    client = make_client(backend)
    task = asyncio.create_task(client.move(0, 0, 1000, absolute=False))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if any(method == "POST" for method, *_ in backend.requests):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    deletes = [path for method, path, *_ in backend.requests if method == "DELETE"]
    assert len(deletes) == 1 and deletes[0].startswith("/actions/")
    action = next(iter(backend.actions.values()))
    assert action["status"] == "cancelled"
    assert await client.get_position() == types.StagePosition(0, 0, 0)


async def test_move_while_stage_busy_raises_stage_busy():
    backend = SimulatedBackend(action_duration_s=10)
    client = make_client(backend)
    first = asyncio.create_task(client.move(0, 0, 1000, absolute=False))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break
    with pytest.raises(errors.StageBusyError):
        await client.move(1, 0, 0, absolute=False)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_missing_stage_refuses_moves():
    client = make_client(SimulatedBackend(stage_type="MissingStage"))
    with pytest.raises(errors.StageUnavailableError):
        await client.move(1, 0, 0, absolute=False)
    with pytest.raises(errors.StageUnavailableError):
        await client.set_zero_position()


async def test_server_side_action_error_becomes_move_failed(client, backend):
    backend.position = {"x": 0, "y": 0, "z": "broken"}  # type: ignore[dict-item] - force the simulated move to raise
    with pytest.raises(errors.MoveFailedError):
        await client.move(0, 0, 1, absolute=False)


# -- camera settings ----------------------------------------------------------------------------------------------


async def test_camera_settings_round_trip(client, backend):
    assert await client.get_exposure_time_us() == 20000
    await client.set_exposure_time_us(5000)
    assert await client.get_exposure_time_us() == 5000
    put = [r for r in backend.requests if r[0] == "PUT"][-1]
    assert put[1] == "/instrument/settings"
    assert put[2] == {"camera": {"picamera": {"shutter_speed": 5000}}}

    await client.set_analog_gain(2.5)
    assert await client.get_analog_gain() == 2.5
    await client.set_digital_gain(1.5)
    assert await client.get_digital_gain() == 1.5

    assert await client.get_exposure_mode() is types.ExposureMode.AUTO
    await client.set_exposure_mode(types.ExposureMode.NIGHT_PREVIEW)
    assert backend.settings["camera"]["picamera"]["exposure_mode"] == "nightpreview"
    assert await client.get_exposure_mode() is types.ExposureMode.NIGHT_PREVIEW

    await client.set_white_balance_mode(types.WhiteBalanceMode.TUNGSTEN)
    assert await client.get_white_balance_mode() is types.WhiteBalanceMode.TUNGSTEN
    await client.set_white_balance_gains(1.9, 1.1)
    assert await client.get_white_balance_gains() == types.WhiteBalanceGains(red=1.9, blue=1.1)
    assert await client.get_white_balance_mode() is types.WhiteBalanceMode.OFF

    await client.set_jpeg_quality(80)
    assert await client.get_jpeg_quality() == 80
    assert await client.get_stream_resolution() == types.Resolution(832, 624)
    assert await client.get_image_resolution() == types.Resolution(3280, 2464)


async def test_missing_camera_reports_unsupported_settings_and_unavailable_camera():
    client = make_client(SimulatedBackend(camera_type="MissingCamera"))
    with pytest.raises(errors.CameraSettingUnsupportedError):
        await client.get_exposure_time_us()
    with pytest.raises(errors.CameraSettingUnsupportedError):
        await client.set_analog_gain(1.0)
    with pytest.raises(errors.CameraUnavailableError):
        await client.grab_preview_frame()
    with pytest.raises(errors.CameraUnavailableError):
        await client.acquire_image(types.CaptureResolution.PREVIEW)
    with pytest.raises(errors.CameraUnavailableError):
        await client.measure_sharpness()
    # Resolutions are generic camera settings and still readable.
    assert await client.get_stream_resolution() == types.Resolution(832, 624)


async def test_unknown_exposure_mode_from_server_is_reported(client, backend):
    backend.settings["camera"]["picamera"]["exposure_mode"] = "lunar"
    with pytest.raises(errors.CameraSettingUnsupportedError, match="lunar"):
        await client.get_exposure_mode()


# -- acquisition and storage --------------------------------------------------------------------------------------


async def test_preview_frame_and_acquire_image(client, backend):
    assert await client.grab_preview_frame() == SIMULATED_JPEG

    image = await client.acquire_image(types.CaptureResolution.FULL)
    assert image == SIMULATED_JPEG
    method, path, body, _params, headers = backend.requests[-1]
    assert (method, path) == ("POST", "/actions/camera/ram-capture")
    assert body == {"use_video_port": False, "bayer": False}
    assert headers.get("Accept") == "image/jpeg"

    await client.acquire_image(types.CaptureResolution.PREVIEW)
    assert backend.requests[-1][2] == {"use_video_port": True, "bayer": False}


async def test_capture_storage_round_trip(client, backend):
    info = await client.capture_to_storage("cells", ["batch-1"], types.CaptureResolution.FULL)
    assert info.name == "cells.jpeg"
    assert info.tags == ["batch-1"]
    body = next(b for m, p, b, *_ in backend.requests if p == "/actions/camera/capture")
    assert body["use_video_port"] is False and body["filename"] == "cells" and body["temporary"] is False

    listed = await client.list_captures()
    assert [c.id for c in listed] == [info.id]

    assert await client.download_capture(info.id) == SIMULATED_JPEG
    assert await client.download_capture(info.id, thumbnail=True) == SIMULATED_JPEG
    assert backend.requests[-1][3] == {"thumbnail": "true"}

    metadata = json.loads((await client.read_capture_metadata(info.id)).decode("utf-8"))
    assert metadata["image"]["name"] == "cells.jpeg"

    assert await client.set_capture_tags(info.id, ["x"]) == ["x"]
    await client.delete_capture(info.id)
    assert await client.list_captures() == []


async def test_capture_without_filename_omits_it(client, backend):
    info = await client.capture_to_storage("", [], types.CaptureResolution.PREVIEW)
    body = next(b for m, p, b, *_ in backend.requests if p == "/actions/camera/capture")
    assert "filename" not in body
    assert info.name.endswith(".jpeg")


async def test_unknown_capture_id_raises_not_found(client):
    for call in (
        client.download_capture("nope"),
        client.read_capture_metadata("nope"),
        client.set_capture_tags("nope", ["a"]),
        client.delete_capture("nope"),
    ):
        with pytest.raises(errors.CaptureNotFoundError):
            await call


# -- autofocus ----------------------------------------------------------------------------------------------------


async def test_autofocus_routines(client, backend):
    await client.move(0, 0, 500, absolute=True)
    position = await client.fast_autofocus(2000)
    assert isinstance(position, types.StagePosition)
    assert position.z != 500
    body = next(b for m, p, b, *_ in backend.requests if p.endswith("/fast_autofocus"))
    assert body == {"dz": 2000}

    await client.move(0, 0, 500, absolute=True)
    samples = await client.autofocus_sweep([-200, -100, 0, 100, 200])
    assert [s.z for s in samples] == [300, 400, 500, 600, 700]
    assert all(isinstance(s.sharpness, float) for s in samples)

    assert isinstance(await client.measure_sharpness(), float)


async def test_autofocus_without_extension_is_unavailable():
    client = make_client(SimulatedBackend(autofocus_extension=False))
    with pytest.raises(errors.AutofocusUnavailableError):
        await client.fast_autofocus(2000)
    with pytest.raises(errors.AutofocusUnavailableError):
        await client.autofocus_sweep([-1, 0, 1])
    with pytest.raises(errors.AutofocusUnavailableError):
        await client.measure_sharpness()


async def test_autofocus_reports_progress_and_can_be_cancelled():
    backend = SimulatedBackend(action_duration_s=10)
    client = make_client(backend)
    seen: list[float | None] = []
    task = asyncio.create_task(client.fast_autofocus(2000, on_progress=seen.append))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert next(iter(backend.actions.values()))["status"] == "cancelled"


async def test_abort_actions_cancels_in_flight_server_actions():
    backend = SimulatedBackend(action_duration_s=10)
    client = make_client(backend)
    move = asyncio.create_task(client.move(0, 0, 1000, absolute=False))
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            break

    assert await client.abort_actions() == 1
    with pytest.raises(errors.MoveFailedError, match="cancelled"):
        await move
    assert next(iter(backend.actions.values()))["status"] == "cancelled"
    assert await client.abort_actions() == 0
    assert await client.get_position() == types.StagePosition(0, 0, 0)


async def wait_for_action(backend: SimulatedBackend) -> None:
    for _ in range(50):
        await asyncio.sleep(0.005)
        if backend.actions:
            return
    raise AssertionError("no action started")


async def cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_mode_switch_is_refused_while_an_action_is_running():
    backend = SimulatedBackend(action_duration_s=10)
    client = make_client(backend)
    real = SimulatedBackend(base_url=REAL_URL, action_duration_s=10)
    client._http_backend_factory = lambda url, timeout: real

    moving = asyncio.create_task(client.move(0, 0, 1000, absolute=False))
    await wait_for_action(backend)
    with pytest.raises(errors.OperationInProgressError):
        await client.start_real()
    assert client.is_simulated is True
    assert client.base_url == SIMULATED_BASE_URL
    await cancel_task(moving)

    await client.start_real()
    assert client.is_simulated is False
    moving = asyncio.create_task(client.move(0, 0, 1000, absolute=False))
    await wait_for_action(real)
    with pytest.raises(errors.OperationInProgressError):
        await client.start_simulation()
    assert client.is_simulated is False
    await cancel_task(moving)
    await client.start_simulation()
    assert client.is_simulated is True


async def test_download_capture_percent_encodes_the_file_name(client, backend):
    info = await client.capture_to_storage("my scan #2?x", [], types.CaptureResolution.PREVIEW)
    assert info.name == "my scan #2?x.jpeg"
    assert await client.download_capture(info.id) == SIMULATED_JPEG
    _method, path, *_ = backend.requests[-1]
    assert path == f"/captures/{info.id}/download/my%20scan%20%232%3Fx.jpeg"


async def test_action_error_message_prefers_output_over_traceback(client, backend):
    backend.position = {"x": 0, "y": 0, "z": "broken"}  # type: ignore[dict-item]
    with pytest.raises(errors.MoveFailedError) as exc_info:
        await client.move(0, 0, 1, absolute=False)
    message = str(exc_info.value)
    assert "Traceback" not in message
    failed_action = next(iter(backend.actions.values()))
    assert failed_action["status"] == "error"
    assert message.endswith(failed_action["output"])  # the server's str(exception), not the traceback log line


async def test_acquire_image_falls_back_to_a_temporary_stored_capture_when_ram_capture_returns_an_action():
    backend = SimulatedBackend(ram_capture_as_action=True)
    client = make_client(backend)
    image = await client.acquire_image(types.CaptureResolution.FULL)
    assert image == SIMULATED_JPEG
    paths = [(m, p) for m, p, *_ in backend.requests]
    assert ("POST", "/actions/camera/capture") in paths
    capture_body = next(b for m, p, b, *_ in backend.requests if p == "/actions/camera/capture")
    assert capture_body["temporary"] is True
    assert capture_body["use_video_port"] is False
    assert any(m == "DELETE" and p.startswith("/captures/") for m, p in paths)
    assert await client.list_captures() == []
