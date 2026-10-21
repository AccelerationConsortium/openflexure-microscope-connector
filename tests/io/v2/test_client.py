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


# -- travel limits, annotations, get_capture ----------------------------------------------------------------------


def make_limited_client(backend: SimulatedBackend, *, action_poll_interval_s: float = 0.005, **limits) -> V2Client:
    from unitelabs.openflexure_microscope.io.limits import TravelLimits

    return V2Client(
        real_base_url=REAL_URL,
        simulated=True,
        simulated_backend=backend,
        action_poll_interval_s=action_poll_interval_s,
        travel_limits=TravelLimits(**limits),
    )


def posted_moves(backend: SimulatedBackend) -> list:
    return [body for method, path, body, *_ in backend.requests if method == "POST" and path == "/actions/stage/move"]


async def test_moves_outside_the_travel_limits_are_refused_before_anything_is_sent():
    backend = SimulatedBackend()
    backend.settings["stage"]["backlash"] = {"x": 0, "y": 0, "z": 0}  # overshoot is tested separately
    client = make_limited_client(backend, x=(-100, 100), z=(0, 50))
    with pytest.raises(errors.TravelLimitExceededError, match="x=101"):
        await client.move(101, 0, 0, absolute=True)
    await client.move(0, 0, 40, absolute=True)
    with pytest.raises(errors.TravelLimitExceededError, match="z=51"):
        await client.move(0, 0, 11, absolute=False)  # 40 + 11
    assert len(posted_moves(backend)) == 1
    assert await client.get_position() == types.StagePosition(0, 0, 40)
    assert await client.move(100, 12345, 0, absolute=True) == types.StagePosition(100, 12345, 0)  # y unlimited


async def test_travel_limits_are_checked_even_without_a_stage():
    client = make_limited_client(SimulatedBackend(stage_type="MissingStage"), z=(0, 10))
    with pytest.raises(errors.TravelLimitExceededError):
        await client.move(0, 0, 11, absolute=True)


async def test_autofocus_ranges_must_stay_within_the_z_limits():
    backend = SimulatedBackend()
    client = make_limited_client(backend, z=(0, 1000))
    await client.move(0, 0, 500, absolute=True)
    with pytest.raises(errors.TravelLimitExceededError):
        await client.fast_autofocus(2000)  # 500 +- 1000
    with pytest.raises(errors.TravelLimitExceededError):
        await client.autofocus_sweep([-600, 0, 100])
    samples = await client.autofocus_sweep([-100, 0, 100])
    assert [s.z for s in samples] == [400, 500, 600]
    assert isinstance(await client.fast_autofocus(400), types.StagePosition)


async def test_without_limits_no_extra_position_read_is_made(client, backend):
    await client.move(1, 0, 0, absolute=False)
    reads = [p for m, p, *_ in backend.requests if m == "GET" and p == "/instrument/state/stage/position"]
    assert reads == []


async def test_capture_annotations_are_stored_and_read_back(client, backend):
    info = await client.capture_to_storage(
        "annotated", [], types.CaptureResolution.PREVIEW, annotations={"sample": "S-17", "operator": "jake"}
    )
    body = next(b for m, p, b, *_ in backend.requests if p == "/actions/camera/capture")
    assert body["annotations"] == {"sample": "S-17", "operator": "jake"}
    assert info.annotations == [types.Annotation("sample", "S-17"), types.Annotation("operator", "jake")]
    listed = next(c for c in await client.list_captures() if c.id == info.id)
    assert listed.annotations == info.annotations


async def test_get_capture(client):
    info = await client.capture_to_storage("one", ["t"], types.CaptureResolution.PREVIEW)
    assert await client.get_capture(info.id) == info
    with pytest.raises(errors.CaptureNotFoundError):
        await client.get_capture("missing")


def test_from_config_builds_travel_limits():
    from unitelabs.openflexure_microscope.io.limits import TravelLimits

    config = OpenflexureMicroscopeConfig(stage_x_min=-5, stage_x_max=5, stage_z_min=0, stage_z_max=9)
    client = V2Client.from_config(config)
    assert client.travel_limits == TravelLimits(x=(-5, 5), z=(0, 9))
    assert V2Client.from_config(OpenflexureMicroscopeConfig()).travel_limits.enabled is False


async def test_zero_position_is_blocked_while_travel_limits_are_configured():
    backend = SimulatedBackend()
    client = make_limited_client(backend, z=(0, 50))
    await client.move(0, 0, 20, absolute=True)
    with pytest.raises(errors.ZeroPositionBlockedError, match="stage_"):
        await client.set_zero_position()
    assert await client.get_position() == types.StagePosition(0, 0, 20)
    assert not any(p == "/actions/stage/zero" for m, p, *_ in backend.requests)


async def test_relative_move_under_limits_ends_at_the_checked_target_even_if_the_stage_moved_meanwhile():
    backend = SimulatedBackend()
    client = make_limited_client(backend, x=(-1000, 1000), z=(0, 1000))
    await client.move(0, 0, 100, absolute=True)
    read_position = client.get_position

    async def read_then_someone_else_moves():
        position = await read_position()
        backend.position.update(x=300, z=900)  # another client moves the stage after the limit check read
        return position

    client.get_position = read_then_someone_else_moves
    final = await client.move(0, 0, 800, absolute=False)  # checked as 100 + 800 = 900
    assert final.z == 900  # a relative move would have ended at 1700, beyond the z limit
    assert final.x == 300  # an axis this move does not change stays where the other client put it
    assert posted_moves(backend)[-1] == {"z": 900, "absolute": True}


async def test_moves_whose_backlash_overshoot_would_leave_the_limits_are_refused():
    backend = SimulatedBackend()  # the simulated stage corrects 100 steps of backlash on every axis
    client = make_limited_client(backend, z=(0, 1000))
    await client.move(0, 0, 500, absolute=True)
    with pytest.raises(errors.TravelLimitExceededError, match="backlash"):
        await client.move(0, 0, -450, absolute=False)  # ends at 50 but first overshoots to -50
    with pytest.raises(errors.TravelLimitExceededError, match="backlash"):
        await client.move(0, 0, 50, absolute=True)
    assert await client.move(0, 0, 100, absolute=True) == types.StagePosition(0, 0, 100)  # overshoot reaches 0
    assert len(posted_moves(backend)) == 2


async def test_an_axis_outside_its_limits_does_not_block_moves_of_the_other_axes():
    backend = SimulatedBackend()
    client = make_limited_client(backend, x=(100, 900), z=(0, 1000))  # x starts at 0, outside its range
    assert (await client.move(0, 0, 10, absolute=False)).z == 10
    assert (await client.move(0, 0, 20, absolute=True)).z == 20
    assert posted_moves(backend)[-1] == {"z": 20, "absolute": True}  # x is left where it is
    with pytest.raises(errors.TravelLimitExceededError, match="x=50"):
        await client.move(50, 0, 0, absolute=False)
    assert (await client.move(100, 0, 20, absolute=True)).x == 100


async def test_slow_autofocus_range_includes_its_backlash_overshoot():
    backend = SimulatedBackend()
    client = make_limited_client(backend, z=(0, 1000))
    await client.move(0, 0, 300, absolute=True)
    with pytest.raises(errors.TravelLimitExceededError, match="256"):
        await client.autofocus_sweep([-100, 0, 100])  # the server forces 256 steps of backlash: 200 - 256 < 0
    assert len(await client.autofocus_sweep([-40, 0, 40])) == 3  # 260 - 256 = 4 stays inside


async def test_a_second_stage_operation_is_refused_while_one_is_running_under_limits(monkeypatch):
    from unitelabs.openflexure_microscope.io.v2 import client as client_module

    monkeypatch.setattr(client_module, "_MOTION_WAIT_S", 0.1)  # how long a second operation waits for the first
    backend = SimulatedBackend(action_duration_s=5.0)  # long enough to still be running whatever the load
    # A slower poll keeps the move request inside the bounded request log (Python < 3.13 on Windows polls fast).
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.05)
    with pytest.raises(errors.StageBusyError, match="still running"):
        await client.move(0, 0, 10, absolute=False)
    with pytest.raises(errors.StageBusyError, match="still running"):
        await client.fast_autofocus(100)
    assert len(posted_moves(backend)) == 1
    assert await client.abort_actions() == 1
    with pytest.raises(errors.MoveFailedError):
        await first
    backend.action_duration_s = 0.0
    assert (await client.move(0, 0, 10, absolute=False)).z == 10  # free again once the first move ended


async def test_a_move_right_after_stop_waits_for_the_stopped_move_to_wind_down():
    backend = SimulatedBackend(action_duration_s=2.0)
    client = make_limited_client(backend, action_poll_interval_s=0.25, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    assert await client.abort_actions() == 1
    # Retreating right after Stop must not fail while the stopped move's poll loop notices the cancellation.
    assert (await client.move(0, 0, 10, absolute=True)).z == 10
    with pytest.raises(errors.MoveFailedError):
        await first


async def test_stop_while_a_limited_move_is_still_preparing_cancels_it_before_it_starts():
    backend = SimulatedBackend()
    client = make_limited_client(backend, z=(0, 1000))
    reading, release = asyncio.Event(), asyncio.Event()
    read_position = client.get_position

    async def slow_read():
        reading.set()
        await release.wait()
        return await read_position()

    client.get_position = slow_read
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await reading.wait()
    stop = asyncio.create_task(client.abort_actions())  # Stop waits for the stopped move to end
    await asyncio.sleep(0.05)
    release.set()
    assert await stop == 1  # the preparing move counts as stopped
    with pytest.raises(errors.MoveFailedError, match="stopped before it started"):
        await move
    assert posted_moves(backend) == []


async def test_stop_before_the_stage_check_finishes_cancels_moves_and_autofocus_without_limits(client, backend):
    checking, release = asyncio.Event(), asyncio.Event()
    require_stage = client._require_stage

    async def slow_require_stage():
        checking.set()
        await release.wait()
        await require_stage()

    client._require_stage = slow_require_stage
    move = asyncio.create_task(client.move(10, 0, 0, absolute=False))
    await checking.wait()
    stop = asyncio.create_task(client.abort_actions())  # Stop waits for the stopped operation to end
    await asyncio.sleep(0.05)
    release.set()
    assert await stop == 1
    with pytest.raises(errors.MoveFailedError, match="stopped before it started"):
        await move
    checking.clear()
    release.clear()
    autofocus = asyncio.create_task(client.fast_autofocus(100))
    await checking.wait()
    stop = asyncio.create_task(client.abort_actions())  # Stop waits for the stopped operation to end
    await asyncio.sleep(0.05)
    release.set()
    assert await stop == 1
    with pytest.raises(errors.AutofocusFailedError, match="stopped before it started"):
        await autofocus
    assert posted_moves(backend) == []
    assert not [p for m, p, *_ in backend.requests if m == "POST" and "autofocus" in p]


async def test_stop_also_cancels_a_move_waiting_for_the_previous_one():
    backend = SimulatedBackend(action_duration_s=0.6)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    second = asyncio.create_task(client.move(0, 0, 10, absolute=True))  # waits for the first one
    await asyncio.sleep(0.05)
    assert await client.abort_actions() == 2
    with pytest.raises(errors.MoveFailedError):
        await first
    with pytest.raises(errors.MoveFailedError, match="stopped before it started"):
        await second
    assert len(posted_moves(backend)) == 1


async def test_limited_moves_on_a_delta_stage_that_corrects_backlash_are_refused():
    backend = SimulatedBackend(stage_type="SangaDeltaStage")  # the simulated backlash is 100 steps per motor
    client = make_limited_client(backend, z=(0, 1000))
    with pytest.raises(errors.TravelLimitExceededError, match="delta stage"):
        await client.move(0, 0, 500, absolute=True)
    with pytest.raises(errors.TravelLimitExceededError, match="delta stage"):
        await client.autofocus_sweep([-10, 0, 10])  # the slow autofocus always corrects 256 steps of backlash
    assert posted_moves(backend) == []
    backend.settings["stage"]["backlash"] = {"x": 0, "y": 0, "z": 0}
    assert (await client.move(0, 0, 500, absolute=True)).z == 500  # with no backlash the end point check is exact
    assert isinstance(await client.fast_autofocus(100), types.StagePosition)  # corrects no backlash


def position_reads(backend: SimulatedBackend) -> int:
    return len([p for m, p, *_ in backend.requests if m == "GET" and p == "/instrument/state/stage/position"])


async def test_a_failed_status_check_is_retried_and_the_move_completes():
    backend = SimulatedBackend(action_duration_s=0.3)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    real_request = backend.request
    failed = []

    async def flaky(method, path, **kwargs):
        if method == "GET" and "/actions/" in path and not failed:
            failed.append(path)
            raise errors.MicroscopeUnreachableError("network glitch")
        return await real_request(method, path, **kwargs)

    backend.request = flaky
    assert (await client.move(0, 0, 500, absolute=True)).z == 500
    assert failed


async def test_a_move_whose_status_cannot_be_read_is_cancelled_on_the_microscope():
    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.02, z=(0, 1000))
    real_request = backend.request

    async def polls_fail(method, path, **kwargs):
        if method == "GET" and "/actions/" in path:
            raise errors.MicroscopeUnreachableError("network down")
        return await real_request(method, path, **kwargs)

    backend.request = polls_fail
    with pytest.raises(errors.MicroscopeUnreachableError):
        await client.move(0, 0, 500, absolute=True)
    assert [action["status"] for action in backend.actions.values()] == ["cancelled"]


async def test_stop_while_the_move_request_is_in_flight_cancels_the_move():
    backend = SimulatedBackend(action_duration_s=1.0)
    client = make_limited_client(backend, z=(0, 1000))
    real_request = backend.request
    posted, release = asyncio.Event(), asyncio.Event()

    async def slow_answer(method, path, **kwargs):
        response = await real_request(method, path, **kwargs)
        if method == "POST" and "/actions/stage/move" in path:
            posted.set()  # the microscope has started the move, but its answer is still on the way
            await release.wait()
        return response

    backend.request = slow_answer
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await posted.wait()
    stop = asyncio.create_task(client.abort_actions())
    await asyncio.sleep(0.05)
    release.set()
    assert await stop == 1
    with pytest.raises(errors.MoveFailedError, match="stopped"):
        await move
    assert [action["status"] for action in backend.actions.values()] == ["cancelled"]


async def test_a_waiting_move_that_is_stopped_fails_at_once_without_reading_the_stage():
    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    second = asyncio.create_task(client.move(0, 0, 10, absolute=True))  # waits for the first one
    await asyncio.sleep(0.05)
    reads = position_reads(backend)
    assert await client.abort_actions() == 2
    with pytest.raises(errors.MoveFailedError, match="stopped before it started"):
        await second
    with pytest.raises(errors.MoveFailedError):
        await first
    assert position_reads(backend) == reads


async def test_waiting_operations_go_first_come_first_served(monkeypatch):
    from unitelabs.openflexure_microscope.io.v2 import client as client_module

    monkeypatch.setattr(client_module, "_MOTION_WAIT_S", 5.0)
    backend = SimulatedBackend(action_duration_s=0.3)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    lock = client._motion_lock
    release = lock.release
    newcomer = []

    def release_then_a_newcomer_arrives():
        release()
        if not newcomer:  # arrives the moment the lock is free, while the older waiter is between its checks
            newcomer.append(asyncio.ensure_future(client.move(0, 0, 30, absolute=True)))

    lock.release = release_then_a_newcomer_arrives
    first = asyncio.create_task(client.move(0, 0, 10, absolute=True))
    await asyncio.sleep(0.05)
    waiter = asyncio.create_task(client.move(0, 0, 20, absolute=True))
    await first
    await waiter
    await newcomer[0]
    assert [body["z"] for body in posted_moves(backend)] == [10, 20, 30]


async def test_repeated_stops_count_each_operation_once():
    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    second = asyncio.create_task(client.move(0, 0, 10, absolute=True))
    await asyncio.sleep(0.05)
    counts = await asyncio.gather(client.abort_actions(), client.abort_actions())
    assert sorted(counts) == [0, 2]
    for task in (first, second):
        with pytest.raises(errors.MoveFailedError):
            await task


async def test_stop_then_move_works_even_with_a_slow_poll_interval():
    backend = SimulatedBackend(action_duration_s=1.0)
    client = make_limited_client(backend, action_poll_interval_s=1.5, z=(0, 1000))
    first = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    assert await client.abort_actions() == 1
    assert (await client.move(0, 0, 10, absolute=True)).z == 10
    with pytest.raises(errors.MoveFailedError):
        await first


def with_requests(backend: SimulatedBackend, handler):
    """Route the backend's requests through `handler(real_request, method, path, **kwargs)`."""
    real_request = backend.request

    async def request(method, path, **kwargs):
        return await handler(real_request, method, path, **kwargs)

    backend.request = request


@pytest.mark.parametrize("failure", ["network", "http"])
async def test_stop_does_not_count_a_move_the_microscope_did_not_cancel(failure):
    from unitelabs.openflexure_microscope.io.v2.backend import Response

    backend = SimulatedBackend(action_duration_s=0.5)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))

    async def cancel_fails(real_request, method, path, **kwargs):
        if method == "DELETE":
            if failure == "network":
                raise errors.MicroscopeUnreachableError("network glitch")
            return Response(500, {}, b"internal error")
        return await real_request(method, path, **kwargs)

    with_requests(backend, cancel_fails)
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    started = asyncio.get_running_loop().time()
    assert await client.abort_actions() == 0
    assert asyncio.get_running_loop().time() - started < 0.3  # no waiting for a move that keeps going
    assert (await move).z == 500


async def test_a_second_stop_cancels_what_the_first_could_not():
    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    failures = [True]

    async def first_cancel_fails(real_request, method, path, **kwargs):
        if method == "DELETE" and failures:
            failures.pop()
            raise errors.MicroscopeUnreachableError("network glitch")
        return await real_request(method, path, **kwargs)

    with_requests(backend, first_cancel_fails)
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await asyncio.sleep(0.1)
    assert await client.abort_actions() == 0
    assert await client.abort_actions() == 1
    with pytest.raises(errors.MoveFailedError):
        await move


async def test_stop_counts_a_running_capture():
    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.05)
    capture = asyncio.create_task(client.capture_to_storage("x", [], types.CaptureResolution.FULL))
    await asyncio.sleep(0.1)
    assert await client.abort_actions() == 1
    with pytest.raises(errors.CaptureFailedError):
        await capture


async def test_a_move_whose_start_cannot_be_cancelled_stays_tracked():
    backend = SimulatedBackend(action_duration_s=0.5)
    client = make_limited_client(backend, action_poll_interval_s=0.05, z=(0, 1000))
    posted, release = asyncio.Event(), asyncio.Event()

    async def slow_answer_and_failing_cancel(real_request, method, path, **kwargs):
        if method == "DELETE":
            raise errors.MicroscopeUnreachableError("network glitch")
        response = await real_request(method, path, **kwargs)
        if method == "POST" and "/actions/stage/move" in path:
            posted.set()
            await release.wait()
        return response

    with_requests(backend, slow_answer_and_failing_cancel)
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await posted.wait()
    stop = asyncio.create_task(client.abort_actions())
    await asyncio.sleep(0.05)
    release.set()
    await stop
    assert (await move).z == 500  # could not be cancelled, so it is still tracked to the end, not left unwatched


async def test_an_http_error_on_a_status_check_is_retried():
    from unitelabs.openflexure_microscope.io.v2.backend import Response

    backend = SimulatedBackend(action_duration_s=0.3)
    client = make_limited_client(backend, action_poll_interval_s=0.05)
    failed = []

    async def one_server_error(real_request, method, path, **kwargs):
        if method == "GET" and "/actions/" in path and not failed:
            failed.append(path)
            return Response(500, {}, b"internal error")
        return await real_request(method, path, **kwargs)

    with_requests(backend, one_server_error)
    assert (await client.move(0, 0, 500, absolute=True)).z == 500
    assert failed


async def test_three_http_errors_on_status_checks_cancel_the_action():
    from unitelabs.openflexure_microscope.io.v2.backend import Response

    backend = SimulatedBackend(action_duration_s=5.0)
    client = make_limited_client(backend, action_poll_interval_s=0.02)

    async def server_errors(real_request, method, path, **kwargs):
        if method == "GET" and "/actions/" in path:
            return Response(500, {}, b"internal error")
        return await real_request(method, path, **kwargs)

    with_requests(backend, server_errors)
    with pytest.raises(errors.MoveFailedError, match="Polling action"):
        await client.move(0, 0, 500, absolute=True)
    assert [action["status"] for action in backend.actions.values()] == ["cancelled"]


async def test_stop_returns_after_its_wait_even_if_a_stopped_operation_is_still_busy(monkeypatch):
    from unitelabs.openflexure_microscope.io.v2 import client as client_module

    monkeypatch.setattr(client_module, "_STOP_SETTLE_S", 0.1)
    backend = SimulatedBackend()
    client = make_limited_client(backend, z=(0, 1000))
    reading, release = asyncio.Event(), asyncio.Event()
    read_position = client.get_position

    async def stuck_read():
        reading.set()
        await release.wait()  # e.g. a slow network: the read takes longer than Stop waits
        return await read_position()

    client.get_position = stuck_read
    move = asyncio.create_task(client.move(0, 0, 500, absolute=True))
    await reading.wait()
    started = asyncio.get_running_loop().time()
    assert await client.abort_actions() == 1
    assert asyncio.get_running_loop().time() - started < 0.5
    release.set()
    with pytest.raises(errors.MoveFailedError, match="stopped before it started"):
        await move
    assert posted_moves(backend) == []
