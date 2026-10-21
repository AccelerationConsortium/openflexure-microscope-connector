"""
Hardware checks for the v2 client against a real OpenFlexure Microscope (see conftest for how to enable them).

With a stage attached, z is moved by 20 steps and back. With `--allow-autofocus`, one test also starts a fast autofocus
(a z sweep of up to 2000 steps), stops it after a second and moves z back to its starting position. Apart from that,
nothing on the microscope is changed except one test capture that is deleted again.
"""

import asyncio

import pytest

from unitelabs.openflexure_microscope.io import errors, types

pytestmark = pytest.mark.hardware

SMALL_Z_STEP = 20


async def test_server_identity(real_api):
    info = await real_api.get_server_info()
    assert info.version.startswith("2."), info
    assert info.camera_type is not types.CameraType.UNKNOWN
    assert info.stage_type is not types.StageType.UNKNOWN
    assert await real_api.ping() is True


async def test_position_and_backlash_are_readable(real_api):
    position = await real_api.get_position()
    assert isinstance(position, types.StagePosition)
    backlash = await real_api.get_backlash()
    assert isinstance(backlash, types.Backlash)


async def test_small_relative_move_and_back(real_api):
    info = await real_api.get_server_info()
    if info.stage_type is types.StageType.MISSING_STAGE:
        pytest.skip("microscope reports no stage")
    start = await real_api.get_position()
    seen: list[float | None] = []
    up = await real_api.move(0, 0, SMALL_Z_STEP, absolute=False, on_progress=seen.append)
    assert up.z == start.z + SMALL_Z_STEP
    down = await real_api.move(0, 0, -SMALL_Z_STEP, absolute=False)
    assert down.z == start.z
    assert seen[-1] == 1.0


async def test_preview_frame_and_in_memory_capture_are_jpeg(real_api):
    frame = await real_api.grab_preview_frame()
    assert frame[:2] == b"\xff\xd8"
    image = await real_api.acquire_image(types.CaptureResolution.PREVIEW)
    assert image[:2] == b"\xff\xd8"


async def test_camera_settings_are_readable(real_api):
    info = await real_api.get_server_info()
    if info.camera_type is not types.CameraType.PI_CAMERA_STREAMER:
        with pytest.raises(errors.CameraSettingUnsupportedError):
            await real_api.get_exposure_time_us()
        return
    assert isinstance(await real_api.get_exposure_time_us(), int)
    assert isinstance(await real_api.get_analog_gain(), float)
    assert isinstance(await real_api.get_exposure_mode(), types.ExposureMode)
    assert isinstance(await real_api.get_white_balance_mode(), types.WhiteBalanceMode)
    assert isinstance(await real_api.get_white_balance_gains(), types.WhiteBalanceGains)
    assert isinstance(await real_api.get_stream_resolution(), types.Resolution)


async def test_capture_storage_round_trip(real_api):
    info = await real_api.capture_to_storage(
        "unitelabs-connector-test", ["unitelabs-test"], types.CaptureResolution.PREVIEW
    )
    try:
        assert info.id in [c.id for c in await real_api.list_captures()]
        thumbnail = await real_api.download_capture(info.id, thumbnail=True)
        assert thumbnail[:2] == b"\xff\xd8"
        metadata = await real_api.read_capture_metadata(info.id)
        assert b"instrument" in metadata
    finally:
        await real_api.delete_capture(info.id)
    with pytest.raises(errors.CaptureNotFoundError):
        await real_api.read_capture_metadata(info.id)


async def test_sharpness_measurement(real_api):
    info = await real_api.get_server_info()
    if "org.openflexure.autofocus" not in info.extensions:
        pytest.skip("autofocus extension not installed")
    assert isinstance(await real_api.measure_sharpness(), float)


async def test_full_resolution_in_memory_capture_is_jpeg(real_api):
    """Exercises the slow ram-capture path (and the temporary-storage fallback if the server answers with an Action)."""
    image = await real_api.acquire_image(types.CaptureResolution.FULL)
    assert image[:2] == b"\xff\xd8"
    assert len(image) > 100_000


@pytest.mark.autofocus_sweep
async def test_stop_cancels_a_running_fast_autofocus(real_api):
    info = await real_api.get_server_info()
    if "org.openflexure.autofocus" not in info.extensions or info.stage_type is types.StageType.MISSING_STAGE:
        pytest.skip("needs the autofocus extension and a stage")
    start = await real_api.get_position()
    running = asyncio.create_task(real_api.fast_autofocus(2000))
    await asyncio.sleep(1.0)
    cancelled = await real_api.abort_actions()
    if cancelled == 0:
        pytest.skip("autofocus finished before it could be stopped")
    with pytest.raises(errors.AutofocusFailedError, match="cancelled"):
        await running
    # bring the focus back to where it started
    final = await real_api.get_position()
    if final.z != start.z:
        await real_api.move(0, 0, start.z - final.z, absolute=False)
