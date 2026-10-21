"""
In-memory stand-in for an OpenFlexure Microscope Server v2.

`SimulatedBackend` answers the same routes, JSON shapes and status codes as the real server (see the OpenAPI
description at https://build.openflexure.org/openflexure-microscope-server/latest-api.yaml), so the v2 client runs
unchanged in simulation mode and in tests. Long-running actions follow the LabThings model: they complete
synchronously when `action_duration_s` is 0 (HTTP 200) or run in the background and are polled/cancelled through
`/actions/{id}` (HTTP 201).
"""

import asyncio
import base64
import collections.abc
import copy
import dataclasses
import datetime
import json as _json
import re
import time
import traceback
import typing
import uuid

from .backend import Response

SIMULATED_BASE_URL = "http://simulated-microscope:5000/api/v2"
AUTOFOCUS_EXTENSION = "org.openflexure.autofocus"
AUTOFOCUS_LINKS = (
    "autofocus",
    "fast_autofocus",
    "fast_up_down_up_autofocus",
    "measure_sharpness",
    "measure_settling_time",
    "move_and_measure",
)
_RUNNING = ("pending", "running")
REQUEST_LOG_SIZE = 1000
_TUPLE_VALUED_SETTINGS = frozenset({"camera/picamera/awb_gains"})
"""Settings picamera stores as tuples; the real server answers a nested GET of them with HTTP 500."""
"""How many recent requests `SimulatedBackend.requests` keeps (bounded so the default mode cannot leak memory)."""
_NO_MATCH = re.compile("").match("")

SIMULATED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1R"
    "V19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2Nj"
    "Y2NjY2NjY2NjY2NjY2P/wAARCAAGAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
    "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCGiiiuw5z/2Q=="
)
"""An 8x6 pixel JPEG used for every simulated image."""


@dataclasses.dataclass(frozen=True)
class _Call:
    """One dispatched request: method, route match and the decoded request parts."""

    method: str
    match: "re.Match[str]"
    body: object
    params: dict[str, str]
    headers: dict[str, str]


Handler = collections.abc.Callable[[_Call], collections.abc.Awaitable[Response]]


def _now() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _json_response(status_code: int, payload: object) -> Response:
    return Response(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=_json.dumps(payload).encode("utf-8"),
    )


def _jpeg_response() -> Response:
    return Response(status_code=200, headers={"content-type": "image/jpeg"}, content=SIMULATED_JPEG)


def _not_found(what: str) -> Response:
    return _json_response(404, {"message": f"{what} not found"})


def _log_record(message: str, level: str = "ERROR") -> dict[str, object]:
    return {
        "created": _now(),
        "filename": "simulation.py",
        "levelname": level,
        "levelno": 40 if level == "ERROR" else 20,
        "lineno": 0,
        "message": message,
        "name": "openflexure_microscope.simulation",
    }


def _get_nested(data: object, route: str | None) -> tuple[bool, object]:
    if not route:
        return True, data
    current = data
    for key in route.strip("/").split("/"):
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


class SimulatedBackend:
    """A fake OpenFlexure v2 server that keeps stage, camera-settings, capture and action state in memory."""

    def __init__(
        self,
        *,
        stage_type: str = "SangaStage",
        camera_type: str = "PiCameraStreamer",
        autofocus_extension: bool = True,
        action_duration_s: float = 0.0,
        ram_capture_as_action: bool = False,
        base_url: str = SIMULATED_BASE_URL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.action_duration_s = action_duration_s
        self.ram_capture_as_action = ram_capture_as_action
        """Answer `ram-capture` with an Action JSON instead of JPEG bytes (a capture slower than the view timeout)."""
        self.focus_z = 0
        """The z position at which the simulated image is sharpest."""
        self.position: dict[str, int] = {"x": 0, "y": 0, "z": 0}
        self.configuration: dict[str, typing.Any] = {
            "application": {"name": "openflexure-microscope-server", "version": "2.11.0"},
            "stage": {"type": stage_type, "port": None},
            "camera": {"type": camera_type},
        }
        camera_settings: dict[str, typing.Any] = {
            "stream_resolution": [832, 624],
            "image_resolution": [3280, 2464],
            "numpy_resolution": [1312, 976],
            "jpeg_quality": 100,
            "mjpeg_quality": 75,
            "mjpeg_bitrate": -1,
        }
        if camera_type == "PiCameraStreamer":
            camera_settings["picamera"] = {
                "shutter_speed": 20000,
                "analog_gain": 1.0,
                "digital_gain": 1.0,
                "exposure_mode": "auto",
                "awb_mode": "auto",
                "awb_gains": [1.5, 1.2],
                "framerate": 30,
                "saturation": 0,
            }
        self.settings: dict[str, typing.Any] = {
            "id": "openflexure:microscope:00000000-0000-4000-8000-00000000s1m0",
            "name": "Simulated OpenFlexure Microscope",
            "extensions": {},
            "camera": camera_settings,
            "stage": {"backlash": {"x": 100, "y": 100, "z": 100}},
            "captures": {},
        }
        self.captures: dict[str, dict[str, typing.Any]] = {}
        self.capture_metadata: dict[str, dict[str, typing.Any]] = {}
        self.actions: dict[str, dict[str, typing.Any]] = {}
        self.requests: collections.deque[tuple[str, str, object, dict[str, str], dict[str, str]]] = collections.deque(
            maxlen=REQUEST_LOG_SIZE
        )
        """The most recent requests, as (method, relative path, json body, params, headers) - handy for assertions."""
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stage_lock_holder: str | None = None
        self._extensions = [self._autofocus_extension_description()] if autofocus_extension else []
        self._routes: list[tuple[re.Pattern[str], tuple[str, ...], Handler]] = [
            (re.compile(r"/"), ("GET",), self._root),
            (re.compile(r"/extensions"), ("GET",), self._extensions_list),
            (re.compile(r"/instrument/configuration(?:/(?P<route>.+))?"), ("GET",), self._configuration),
            (re.compile(r"/instrument/settings(?:/(?P<route>.+))?"), ("GET", "PUT"), self._settings),
            (re.compile(r"/instrument/state(?:/(?P<route>.+))?"), ("GET",), self._state),
            (re.compile(r"/instrument/stage/type"), ("GET", "PUT"), self._stage_type),
            (re.compile(r"/streams/snapshot"), ("GET",), self._snapshot),
            (re.compile(r"/actions"), ("GET",), self._actions_list),
            (re.compile(r"/actions/(?P<action_id>[^/]+)"), ("GET", "DELETE"), self._action),
            (re.compile(r"/actions/stage/move"), ("GET", "POST"), self._stage_move),
            (re.compile(r"/actions/stage/zero"), ("GET", "POST"), self._stage_zero),
            (re.compile(r"/actions/camera/capture"), ("GET", "POST"), self._camera_capture),
            (re.compile(r"/actions/camera/ram-capture"), ("GET", "POST"), self._camera_ram_capture),
            (re.compile(r"/captures"), ("GET",), self._captures_list),
            (re.compile(r"/captures/(?P<capture_id>[^/]+)"), ("GET", "DELETE"), self._capture),
            (
                re.compile(r"/captures/(?P<capture_id>[^/]+)/download/(?P<filename>[^/]+)"),
                ("GET",),
                self._capture_download,
            ),
            (re.compile(r"/captures/(?P<capture_id>[^/]+)/tags"), ("GET", "PUT", "DELETE"), self._capture_tags),
            (re.compile(r"/captures/(?P<capture_id>[^/]+)/annotations"), ("GET", "PUT"), self._capture_annotations),
        ]
        if autofocus_extension:
            prefix = f"/extensions/{re.escape(AUTOFOCUS_EXTENSION)}"
            self._routes += [
                (re.compile(prefix + r"/fast_autofocus"), ("GET", "POST"), self._fast_autofocus),
                (re.compile(prefix + r"/autofocus"), ("GET", "POST"), self._autofocus_sweep),
                (re.compile(prefix + r"/measure_sharpness"), ("POST",), self._measure_sharpness),
            ]
        # Specific action routes must win over the generic `/actions/{id}` route.
        self._routes.sort(key=lambda item: ("(?P<" in item[0].pattern, -len(item[0].pattern)))

    # -- Backend protocol ----------------------------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: collections.abc.Mapping[str, str] | None = None,
        headers: collections.abc.Mapping[str, str] | None = None,
    ) -> Response:
        """Dispatch a request to the simulated route table."""
        if path.startswith(self.base_url):
            path = path[len(self.base_url) :]
        elif path.startswith(("http://", "https://")):
            return _not_found("route")
        path = "/" + path.strip("/")
        call = _Call(
            method.upper(), typing.cast("re.Match[str]", _NO_MATCH), json, dict(params or {}), dict(headers or {})
        )
        self.requests.append((call.method, path, json, call.params, call.headers))

        for pattern, methods, handler in self._routes:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            if call.method not in methods:
                return _json_response(405, {"message": f"{method} not allowed on {path}"})
            return await handler(dataclasses.replace(call, match=match))

        return _not_found("route")

    async def aclose(self) -> None:
        """Cancel any background actions still running."""
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

    # -- routes: information ---------------------------------------------------------------------------------------

    async def _root(self, _call: _Call) -> Response:
        return _json_response(
            200,
            {
                "@context": "https://www.w3.org/2019/wot/td/v1",
                "id": self.base_url,
                "title": f"OpenFlexure Microscope {self.settings['name']}",
                "description": "Simulated LabThing-based API for OpenFlexure Microscope",
                "properties": {},
                "actions": {},
                "links": [],
            },
        )

    async def _extensions_list(self, _call: _Call) -> Response:
        return _json_response(200, copy.deepcopy(self._extensions))

    async def _configuration(self, call: _Call) -> Response:
        found, value = _get_nested(self.configuration, call.match.group("route"))
        return _json_response(200, value) if found else _not_found("configuration key")

    async def _settings(self, call: _Call) -> Response:
        route = (call.match.group("route") or "").strip("/")
        if call.method == "GET":
            if route in _TUPLE_VALUED_SETTINGS and _get_nested(self.settings, route)[0]:
                return _json_response(500, {"code": 500, "message": "Invalid status argument", "name": "TypeError"})
            found, value = _get_nested(self.settings, route)
            return _json_response(200, value) if found else _not_found("settings key")
        # PUT: the real server only accepts a JSON object, merged into the addressed section.
        found, section = _get_nested(self.settings, route)
        if not found:
            return _not_found("settings key")
        if not isinstance(call.body, dict) or not isinstance(section, dict):
            return _json_response(
                400,
                {
                    "code": 400,
                    "message": "The browser (or proxy) sent a request that this server could not understand.",
                    "name": "Bad Request",
                },
            )
        self._merge_settings(section, call.body)
        return _json_response(200, section)

    def _merge_settings(self, target: dict[str, typing.Any], source: dict[str, typing.Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._merge_settings(target[key], value)
            else:
                target[key] = value
                if key == "awb_gains" and "awb_mode" in target:
                    target["awb_mode"] = "off"

    async def _state(self, call: _Call) -> Response:
        state = {"camera": {}, "stage": {"position": dict(self.position)}}
        found, value = _get_nested(state, call.match.group("route"))
        return _json_response(200, value) if found else _not_found("state key")

    async def _stage_type(self, call: _Call) -> Response:
        if call.method == "PUT":
            self.configuration["stage"]["type"] = call.body
        return _json_response(200, self.configuration["stage"]["type"])

    async def _snapshot(self, _call: _Call) -> Response:
        return _jpeg_response()

    # -- routes: actions -----------------------------------------------------------------------------------------

    async def _actions_list(self, _call: _Call) -> Response:
        return _json_response(200, [copy.deepcopy(action) for action in self.actions.values()])

    async def _action(self, call: _Call) -> Response:
        action = self.actions.get(call.match.group("action_id"))
        if action is None:
            return _not_found("action")
        if call.method == "DELETE" and action["status"] in _RUNNING:
            task = self._tasks.pop(action["id"], None)
            if task is not None:
                task.cancel()
            self._finish(action, "cancelled")
        return _json_response(200, copy.deepcopy(action))

    async def _stage_move(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "move"])
        args = call.body if isinstance(call.body, dict) else {}
        absolute = bool(args.get("absolute", False))
        requested = {axis: args.get(axis) for axis in ("x", "y", "z")}

        def work() -> object:
            for axis, value in requested.items():
                if value is None:
                    continue
                self.position[axis] = int(value) if absolute else self.position[axis] + int(value)
            return dict(self.position)

        return self._start_action("move", args, work, uses_stage=True)

    async def _stage_zero(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "zeroStage"])

        def work() -> object:
            self.position = {"x": 0, "y": 0, "z": 0}
            return {"position": dict(self.position)}

        return self._start_action("zeroStage", call.body, work, uses_stage=True)

    async def _camera_capture(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "capture"])
        args = call.body if isinstance(call.body, dict) else {}

        def work() -> object:
            return self._store_capture(args)

        return self._start_action("capture", args, work, uses_stage=False)

    async def _camera_ram_capture(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "ramCapture"])
        if self.ram_capture_as_action:
            return self._start_action("ramCapture", call.body, lambda: None, uses_stage=False)
        return _jpeg_response()

    def _start_action(
        self, name: str, body: object, work: collections.abc.Callable[[], object], *, uses_stage: bool
    ) -> Response:
        action_id = str(uuid.uuid4())
        action: dict[str, typing.Any] = {
            "id": action_id,
            "action": name,
            "href": f"{self.base_url}/actions/{action_id}",
            "status": "pending",
            "progress": None,
            "input": body,
            "output": None,
            "log": [],
            "data": {},
            "links": {"self": {"href": f"{self.base_url}/actions/{action_id}", "mimetype": "application/json"}},
            "timeRequested": _now(),
            "timeCompleted": None,
        }
        self.actions[action_id] = action

        if uses_stage and self._stage_lock_holder is not None:
            self._fail(action, "Could not acquire the stage lock within 1 second: the stage is busy.", "LockError")
            self._finish(action, "error")
            return _json_response(200, copy.deepcopy(action))

        if self.action_duration_s <= 0:
            self._complete(action, work)
            return _json_response(200, copy.deepcopy(action))

        action["status"] = "running"
        action["progress"] = 0
        if uses_stage:
            self._stage_lock_holder = action_id
        self._tasks[action_id] = asyncio.create_task(self._run_later(action, work))
        return _json_response(201, copy.deepcopy(action))

    async def _run_later(self, action: dict[str, typing.Any], work: collections.abc.Callable[[], object]) -> None:
        started = time.monotonic()
        try:
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= self.action_duration_s:
                    break
                action["progress"] = int(100 * elapsed / self.action_duration_s)
                await asyncio.sleep(min(0.01, self.action_duration_s - elapsed))
            self._complete(action, work)
        except asyncio.CancelledError:
            if action["status"] in _RUNNING:
                self._finish(action, "cancelled")
            raise
        finally:
            self._tasks.pop(action["id"], None)

    def _complete(self, action: dict[str, typing.Any], work: collections.abc.Callable[[], object]) -> None:
        try:
            action["output"] = work()
        except Exception as exc:  # noqa: BLE001 - mirrors the server: any exception ends the action with status error
            self._fail(action, str(exc), traceback.format_exc())
            self._finish(action, "error")
            return
        action["progress"] = 100
        self._finish(action, "completed")

    @staticmethod
    def _fail(action: dict[str, typing.Any], message: str, trace: str) -> None:
        """Record a failure like python-labthings does: `output` = str(exception), log = message then traceback."""
        action["output"] = message
        action["log"].append(_log_record(message))
        if not trace.startswith("Traceback"):
            trace = f"Traceback (most recent call last):\n  (simulated)\n{trace}: {message}"
        action["log"].append(_log_record(trace))

    def _finish(self, action: dict[str, typing.Any], status: str) -> None:
        action["status"] = status
        action["timeCompleted"] = _now()
        if self._stage_lock_holder == action["id"]:
            self._stage_lock_holder = None

    # -- routes: captures ----------------------------------------------------------------------------------------

    def _store_capture(self, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
        capture_id = str(uuid.uuid4())
        name = str(
            args.get("filename") or datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S-%f")
        )
        if "." not in name:
            name = f"{name}.jpeg"
        base = f"{self.base_url}/captures/{capture_id}"
        capture = {
            "id": capture_id,
            "name": name,
            "path": f"/var/openflexure/data/micrographs/{name}",
            "time": _now(),
            "format": "jpeg",
            "tags": [str(tag) for tag in (args.get("tags") or [])],
            "annotations": dict(args.get("annotations") or {}),
            "dataset": {"id": str(uuid.uuid4()), "name": "", "type": "capture"},
            "links": {
                "self": {"href": base, "mimetype": "application/json"},
                "tags": {"href": f"{base}/tags", "mimetype": "application/json"},
                "annotations": {"href": f"{base}/annotations", "mimetype": "application/json"},
                "download": {"href": f"{base}/download/{name}", "mimetype": "image/jpeg"},
            },
        }
        self.captures[capture_id] = capture
        self.capture_metadata[capture_id] = {
            "dataset": dict(capture["dataset"]),
            "image": {key: capture[key] for key in ("id", "time", "format", "name", "tags", "annotations")},
            "instrument": {
                "id": self.settings["id"],
                "configuration": copy.deepcopy(self.configuration),
                "settings": copy.deepcopy(self.settings),
                "state": {"camera": {}, "stage": {"position": dict(self.position)}},
            },
        }
        return copy.deepcopy(capture)

    async def _captures_list(self, _call: _Call) -> Response:
        return _json_response(200, [copy.deepcopy(c) for c in self.captures.values()])

    async def _capture(self, call: _Call) -> Response:
        capture_id = call.match.group("capture_id")
        capture = self.captures.get(capture_id)
        if capture is None:
            return _not_found("capture")
        if call.method == "DELETE":
            del self.captures[capture_id]
            self.capture_metadata.pop(capture_id, None)
            return Response(status_code=204, headers={}, content=b"")
        full = copy.deepcopy(capture)
        full["metadata"] = copy.deepcopy(self.capture_metadata.get(capture_id, {}))
        return _json_response(200, full)

    async def _capture_download(self, call: _Call) -> Response:
        if call.match.group("capture_id") not in self.captures:
            return _not_found("capture")
        return _jpeg_response()

    async def _capture_tags(self, call: _Call) -> Response:
        capture = self.captures.get(call.match.group("capture_id"))
        if capture is None:
            return _not_found("capture")
        if call.method == "PUT":
            capture["tags"] = [str(tag) for tag in (call.body if isinstance(call.body, list) else [])]
        elif call.method == "DELETE":
            capture["tags"] = []
        return _json_response(200, list(capture["tags"]))

    async def _capture_annotations(self, call: _Call) -> Response:
        capture = self.captures.get(call.match.group("capture_id"))
        if capture is None:
            return _not_found("capture")
        if call.method == "PUT" and isinstance(call.body, dict):
            capture["annotations"].update({str(k): str(v) for k, v in call.body.items()})
        return _json_response(200, dict(capture["annotations"]))

    # -- routes: autofocus extension -----------------------------------------------------------------------------

    def _autofocus_extension_description(self) -> dict[str, typing.Any]:
        prefix = f"{self.base_url}/extensions/{AUTOFOCUS_EXTENSION}"
        return {
            "title": AUTOFOCUS_EXTENSION,
            "description": "Simulated autofocus extension",
            "pythonName": AUTOFOCUS_EXTENSION,
            "pythonObject": "openflexure_microscope.api.default_extensions.autofocus.AutofocusExtension",
            "meta": {},
            "links": {
                name: {
                    "href": f"{prefix}/{name}",
                    "methods": ["GET", "POST"] if name != "measure_sharpness" else ["POST"],
                    "description": "",
                }
                for name in AUTOFOCUS_LINKS
            },
        }

    def _sharpness_at(self, z: int) -> float:
        return float(100.0 / (1.0 + ((z - self.focus_z) / 100.0) ** 2))

    async def _fast_autofocus(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "fast_autofocus"])
        args = call.body if isinstance(call.body, dict) else {}
        dz = int(args.get("dz", 2000))

        def work() -> object:
            start_z = self.position["z"]
            step = max(1, abs(dz) // 10)
            samples = [start_z - dz // 2 + i * step for i in range(11)]
            best = min(samples, key=lambda z: abs(z - self.focus_z))
            self.position["z"] = best if best != start_z else start_z + step
            return {
                "jpeg_times": [i * 0.05 for i in range(len(samples))],
                "jpeg_sizes": [int(1000 * self._sharpness_at(z)) for z in samples],
                "stage_times": [i * 0.05 for i in range(len(samples))],
                "stage_positions": [[self.position["x"], self.position["y"], z] for z in samples],
            }

        return self._start_action("fast_autofocus", args, work, uses_stage=True)

    async def _autofocus_sweep(self, call: _Call) -> Response:
        if call.method == "GET":
            return _json_response(200, [a for a in self.actions.values() if a["action"] == "autofocus"])
        args = call.body if isinstance(call.body, dict) else {}
        offsets = [int(d) for d in (args.get("dz") or [-300, -200, -100, 0, 100, 200, 300])]

        def work() -> object:
            start_z = self.position["z"]
            positions = [start_z + offset for offset in offsets]
            sharpnesses = [self._sharpness_at(z) for z in positions]
            self.position["z"] = positions[sharpnesses.index(max(sharpnesses))]
            return [positions, sharpnesses]

        return self._start_action("autofocus", args, work, uses_stage=True)

    async def _measure_sharpness(self, _call: _Call) -> Response:
        if self.configuration["camera"]["type"] != "PiCameraStreamer":
            return _json_response(500, {"message": "AttributeError: 'MissingCamera' object has no attribute 'array'"})
        return _json_response(200, {"sharpness": self._sharpness_at(self.position["z"])})
