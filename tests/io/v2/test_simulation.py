import asyncio
import time

import pytest

from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_BASE_URL, SimulatedBackend

AUTOFOCUS = "/extensions/org.openflexure.autofocus"


@pytest.fixture
def backend() -> SimulatedBackend:
    return SimulatedBackend()


async def poll_until_done(backend: SimulatedBackend, action: dict, timeout_s: float = 5.0) -> dict:
    # Bounded by wall-clock time, not by a poll count: on Windows before Python 3.13 the event-loop clock ticks every
    # ~15 ms, so short sleeps return at once and a fixed number of polls can finish before any time has passed.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = await backend.request("GET", action["links"]["self"]["href"])
        action = response.json()
        if action["status"] not in ("pending", "running"):
            return action
        await asyncio.sleep(0.005)
    msg = f"action did not finish within {timeout_s} s"
    raise AssertionError(msg)


async def test_root_and_configuration(backend):
    root = await backend.request("GET", "/")
    assert root.status_code == 200
    assert "title" in root.json()

    configuration = (await backend.request("GET", "/instrument/configuration")).json()
    assert configuration["application"]["version"] == "2.11.0"
    assert configuration["stage"]["type"] == "SangaStage"
    assert configuration["camera"]["type"] == "PiCameraStreamer"

    nested = await backend.request("GET", "/instrument/configuration/application/version")
    assert nested.json() == "2.11.0"


async def test_hardware_kinds_are_configurable():
    backend = SimulatedBackend(stage_type="MissingStage", camera_type="MissingCamera")
    configuration = (await backend.request("GET", "/instrument/configuration")).json()
    assert configuration["stage"]["type"] == "MissingStage"
    assert configuration["camera"]["type"] == "MissingCamera"
    # A missing camera has no picamera settings tree, exactly like the real server.
    response = await backend.request("GET", "/instrument/settings/camera/picamera/shutter_speed")
    assert response.status_code == 404


async def test_initial_position_is_origin(backend):
    response = await backend.request("GET", "/instrument/state/stage/position")
    assert response.json() == {"x": 0, "y": 0, "z": 0}
    state = (await backend.request("GET", "/instrument/state")).json()
    assert state["stage"]["position"] == {"x": 0, "y": 0, "z": 0}


async def test_relative_move_completes_immediately_without_duration(backend):
    response = await backend.request("POST", "/actions/stage/move/", json={"x": 100, "z": -20, "absolute": False})
    assert response.status_code == 200
    action = response.json()
    assert action["status"] == "completed"
    assert action["output"] == {"x": 100, "y": 0, "z": -20}
    assert action["links"]["self"]["href"].startswith(SIMULATED_BASE_URL + "/actions/")


async def test_absolute_move_only_touches_given_axes(backend):
    await backend.request("POST", "/actions/stage/move/", json={"x": 100, "y": 50, "z": 10, "absolute": False})
    response = await backend.request("POST", "/actions/stage/move/", json={"x": 5, "absolute": True})
    assert response.json()["output"] == {"x": 5, "y": 50, "z": 10}


async def test_long_move_is_polled_and_reports_progress():
    backend = SimulatedBackend(action_duration_s=0.05)
    response = await backend.request("POST", "/actions/stage/move/", json={"y": 30, "absolute": False})
    assert response.status_code == 201
    started = response.json()
    assert started["status"] in ("pending", "running")
    assert started["id"] == started["links"]["self"]["href"].rsplit("/", 1)[-1]

    finished = await poll_until_done(backend, started)
    assert finished["status"] == "completed"
    assert finished["progress"] == 100
    assert finished["output"] == {"x": 0, "y": 30, "z": 0}
    assert (await backend.request("GET", "/instrument/state/stage/position")).json() == {"x": 0, "y": 30, "z": 0}


async def test_running_action_can_be_cancelled():
    backend = SimulatedBackend(action_duration_s=10)
    started = (await backend.request("POST", "/actions/stage/move/", json={"x": 1000, "absolute": False})).json()
    cancelled = await backend.request("DELETE", f"/actions/{started['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert (await backend.request("GET", "/instrument/state/stage/position")).json() == {"x": 0, "y": 0, "z": 0}
    listed = (await backend.request("GET", "/actions")).json()
    assert [a["id"] for a in listed] == [started["id"]]


async def test_second_stage_action_while_busy_errors_like_the_lock_timeout():
    backend = SimulatedBackend(action_duration_s=10)
    first = (await backend.request("POST", "/actions/stage/move/", json={"x": 1000, "absolute": False})).json()
    second = (await backend.request("POST", "/actions/stage/move/", json={"x": 1, "absolute": False})).json()
    assert second["status"] == "error"
    assert any("lock" in entry["message"].lower() for entry in second["log"])
    await backend.request("DELETE", f"/actions/{first['id']}")


async def test_unknown_action_id_is_404(backend):
    assert (await backend.request("GET", "/actions/nope")).status_code == 404
    assert (await backend.request("DELETE", "/actions/nope")).status_code == 404


async def test_zero_resets_position_without_moving(backend):
    await backend.request("POST", "/actions/stage/move/", json={"x": 7, "y": 8, "z": 9, "absolute": True})
    response = await backend.request("POST", "/actions/stage/zero/")
    assert response.json()["status"] == "completed"
    assert response.json()["output"] == {"position": {"x": 0, "y": 0, "z": 0}}


async def test_settings_nested_get_put_and_404(backend):
    assert (await backend.request("GET", "/instrument/settings/camera/picamera/shutter_speed")).json() == 20000
    # Observed on a real 2.11.0 server: a nested PUT only accepts a JSON object, which is merged into that section.
    bare = await backend.request("PUT", "/instrument/settings/camera/picamera/shutter_speed", json=5000)
    assert bare.status_code == 400
    section = await backend.request("PUT", "/instrument/settings/camera/picamera", json={"shutter_speed": 5000})
    assert section.status_code == 200
    assert section.json()["shutter_speed"] == 5000
    root = await backend.request("PUT", "/instrument/settings", json={"camera": {"jpeg_quality": 90}})
    assert root.status_code == 200
    assert root.json()["camera"]["jpeg_quality"] == 90
    assert (await backend.request("GET", "/instrument/settings/camera/picamera/shutter_speed")).json() == 5000

    full = (await backend.request("GET", "/instrument/settings")).json()
    assert full["camera"]["picamera"]["shutter_speed"] == 5000
    assert full["camera"]["stream_resolution"] == [832, 624]
    assert full["stage"]["backlash"] == {"x": 100, "y": 100, "z": 100}

    assert (await backend.request("GET", "/instrument/settings/camera/picamera/does_not_exist")).status_code == 404
    assert (
        await backend.request("PUT", "/instrument/settings/camera/picamera/does_not_exist", json={"a": 1})
    ).status_code == 404


async def test_nested_read_of_awb_gains_fails_like_the_real_server(backend):
    # picamera stores awb_gains as a tuple, which Flask misreads as (body, status): HTTP 500 "Invalid status argument".
    response = await backend.request("GET", "/instrument/settings/camera/picamera/awb_gains")
    assert response.status_code == 500
    assert "Invalid status argument" in response.json()["message"]
    picamera = (await backend.request("GET", "/instrument/settings/camera/picamera")).json()
    assert picamera["awb_gains"] == [1.5, 1.2]


async def test_setting_awb_gains_switches_white_balance_off(backend):
    await backend.request("PUT", "/instrument/settings", json={"camera": {"picamera": {"awb_gains": [1.8, 1.1]}}})
    picamera = (await backend.request("GET", "/instrument/settings/camera/picamera")).json()
    assert picamera["awb_gains"] == [1.8, 1.1]
    assert picamera["awb_mode"] == "off"


async def test_snapshot_and_ram_capture_return_jpeg(backend):
    snapshot = await backend.request("GET", "/streams/snapshot")
    assert snapshot.headers["content-type"] == "image/jpeg"
    assert snapshot.content[:2] == b"\xff\xd8"
    assert snapshot.content[-2:] == b"\xff\xd9"

    capture = await backend.request(
        "POST",
        "/actions/camera/ram-capture/",
        json={"use_video_port": True, "bayer": False},
        headers={"Accept": "image/jpeg"},
    )
    assert capture.status_code == 200
    assert capture.content[:2] == b"\xff\xd8"


async def test_capture_lifecycle(backend):
    response = await backend.request(
        "POST",
        "/actions/camera/capture/",
        json={"use_video_port": False, "bayer": False, "filename": "sample", "tags": ["a"], "temporary": False},
    )
    capture = response.json()["output"]
    assert capture["name"] == "sample.jpeg"
    assert capture["tags"] == ["a"]
    assert capture["format"] == "jpeg"
    capture_id = capture["id"]

    listed = (await backend.request("GET", "/captures")).json()
    assert [c["id"] for c in listed] == [capture_id]

    full = (await backend.request("GET", f"/captures/{capture_id}")).json()
    assert full["metadata"]["image"]["name"] == "sample.jpeg"
    assert "instrument" in full["metadata"]

    image = await backend.request("GET", f"/captures/{capture_id}/download/sample.jpeg")
    assert image.content[:2] == b"\xff\xd8"
    thumbnail = await backend.request(
        "GET", f"/captures/{capture_id}/download/sample.jpeg", params={"thumbnail": "true"}
    )
    assert thumbnail.content[:2] == b"\xff\xd8"

    tags = await backend.request("PUT", f"/captures/{capture_id}/tags", json=["x", "y"])
    assert tags.json() == ["x", "y"]
    assert (await backend.request("GET", f"/captures/{capture_id}/tags")).json() == ["x", "y"]

    assert (await backend.request("DELETE", f"/captures/{capture_id}")).status_code == 204
    assert (await backend.request("GET", f"/captures/{capture_id}")).status_code == 404
    assert (await backend.request("GET", f"/captures/{capture_id}/download/sample.jpeg")).status_code == 404


async def test_capture_without_filename_gets_timestamp_name(backend):
    response = await backend.request("POST", "/actions/camera/capture/", json={"use_video_port": True, "bayer": False})
    assert response.json()["output"]["name"].endswith(".jpeg")


async def test_extensions_list_autofocus_links(backend):
    extensions = (await backend.request("GET", "/extensions")).json()
    titles = [e["title"] for e in extensions]
    assert "org.openflexure.autofocus" in titles
    autofocus = next(e for e in extensions if e["title"] == "org.openflexure.autofocus")
    assert autofocus["links"]["fast_autofocus"]["href"] == f"{SIMULATED_BASE_URL}{AUTOFOCUS}/fast_autofocus"
    assert "POST" in autofocus["links"]["measure_sharpness"]["methods"]


async def test_autofocus_extension_can_be_absent():
    backend = SimulatedBackend(autofocus_extension=False)
    assert (await backend.request("GET", "/extensions")).json() == []
    assert (await backend.request("POST", f"{AUTOFOCUS}/fast_autofocus", json={"dz": 2000})).status_code == 404


async def test_fast_autofocus_moves_z_and_reports_arrays(backend):
    response = await backend.request("POST", f"{AUTOFOCUS}/fast_autofocus", json={"dz": 2000})
    action = response.json()
    assert action["status"] == "completed"
    assert set(action["output"]) >= {"jpeg_times", "jpeg_sizes", "stage_times", "stage_positions"}
    position = (await backend.request("GET", "/instrument/state/stage/position")).json()
    assert position["z"] != 0
    assert position["x"] == 0 and position["y"] == 0


async def test_autofocus_sweep_returns_positions_and_sharpnesses(backend):
    await backend.request("POST", "/actions/stage/move/", json={"z": 500, "absolute": True})
    dz = [-200, -100, 0, 100, 200]
    action = (await backend.request("POST", f"{AUTOFOCUS}/autofocus", json={"dz": dz})).json()
    positions, sharpnesses = action["output"]
    assert positions == [300, 400, 500, 600, 700]
    assert len(sharpnesses) == 5
    best = positions[sharpnesses.index(max(sharpnesses))]
    assert (await backend.request("GET", "/instrument/state/stage/position")).json()["z"] == best


async def test_measure_sharpness(backend):
    response = await backend.request("POST", f"{AUTOFOCUS}/measure_sharpness")
    assert response.status_code == 200
    assert isinstance(response.json()["sharpness"], float)


async def test_measure_sharpness_fails_without_camera():
    backend = SimulatedBackend(camera_type="MissingCamera")
    response = await backend.request("POST", f"{AUTOFOCUS}/measure_sharpness")
    assert response.status_code == 500


async def test_unknown_route_and_method(backend):
    assert (await backend.request("GET", "/does/not/exist")).status_code == 404
    assert (await backend.request("DELETE", "/instrument/state")).status_code == 405


async def test_request_log_is_bounded():
    from unitelabs.openflexure_microscope.io.v2.simulation import REQUEST_LOG_SIZE

    backend = SimulatedBackend()
    for _ in range(REQUEST_LOG_SIZE + 50):
        await backend.request("GET", "/instrument/state/stage/position")
    assert len(backend.requests) == REQUEST_LOG_SIZE
    assert backend.requests[-1][1] == "/instrument/state/stage/position"


async def test_failed_action_mirrors_the_server_error_shape(backend):
    backend.position = {"x": 0, "y": 0, "z": "broken"}  # type: ignore[dict-item]
    action = (await backend.request("POST", "/actions/stage/move/", json={"z": 1, "absolute": False})).json()
    assert action["status"] == "error"
    # python-labthings stores str(exception) in `output` and logs the message followed by the traceback.
    assert isinstance(action["output"], str) and action["output"]
    assert len(action["log"]) == 2
    assert action["log"][-1]["message"].startswith("Traceback")


async def test_busy_stage_error_shape_includes_traceback():
    backend = SimulatedBackend(action_duration_s=10)
    first = (await backend.request("POST", "/actions/stage/move/", json={"x": 1000, "absolute": False})).json()
    second = (await backend.request("POST", "/actions/stage/move/", json={"x": 1, "absolute": False})).json()
    assert "lock" in second["output"].lower()
    assert second["log"][-1]["message"].startswith("Traceback")
    await backend.request("DELETE", f"/actions/{first['id']}")


async def test_ram_capture_can_answer_with_an_action_like_a_slow_server():
    backend = SimulatedBackend(ram_capture_as_action=True)
    response = await backend.request(
        "POST",
        "/actions/camera/ram-capture/",
        json={"use_video_port": False, "bayer": False},
        headers={"Accept": "image/jpeg"},
    )
    assert response.headers["content-type"] == "application/json"
    action = response.json()
    assert action["status"] == "completed"
    assert action["output"] is None
