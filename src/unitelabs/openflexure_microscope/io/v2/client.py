"""
`MicroscopeApi` implementation for the OpenFlexure Microscope Server v2 HTTP API (`/api/v2`).

Long-running server calls are LabThings *actions*: `POST` returns the finished action (HTTP 200) or a running one
(HTTP 201) whose `links.self.href` is polled until it leaves `pending`/`running`; `DELETE` on that link cancels it.
"""

import asyncio
import collections.abc
import contextlib
import json as _json
import logging
import typing
import urllib.parse

from ..api import Capability, MicroscopeApi, ProgressCallback
from ..errors import (
    AutofocusFailedError,
    AutofocusUnavailableError,
    CameraSettingUnsupportedError,
    CameraUnavailableError,
    CaptureFailedError,
    CaptureNotFoundError,
    MicroscopeUnreachableError,
    MoveFailedError,
    OperationInProgressError,
    StageBusyError,
    StageUnavailableError,
)
from ..types import (
    Backlash,
    CameraType,
    CaptureInfo,
    CaptureResolution,
    ExposureMode,
    Resolution,
    ServerInfo,
    SharpnessSample,
    StagePosition,
    StageType,
    WhiteBalanceGains,
    WhiteBalanceMode,
)
from .backend import Backend, HttpBackend, Response
from .simulation import AUTOFOCUS_EXTENSION, SimulatedBackend

if typing.TYPE_CHECKING:
    from ...config import OpenflexureMicroscopeConfig

log = logging.getLogger(__name__)

_RUNNING = ("pending", "running")
_PICAMERA = "camera/picamera"

HttpBackendFactory = collections.abc.Callable[[str, float], Backend]


def _default_http_backend_factory(base_url: str, timeout_s: float) -> Backend:
    return HttpBackend(base_url, timeout_s=timeout_s)


def _describe(response: Response) -> str:
    text = response.content.decode("utf-8", errors="replace").strip()
    return f"HTTP {response.status_code}: {text[:300]}" if text else f"HTTP {response.status_code}"


def _log_messages(action: collections.abc.Mapping[str, typing.Any]) -> list[str]:
    return [str(entry.get("message", "")) for entry in (action.get("log") or []) if isinstance(entry, dict)]


def _error_message(action: collections.abc.Mapping[str, typing.Any]) -> str:
    """
    Return the most useful human-readable reason for a failed action.

    python-labthings stores `str(exception)` in `output` and logs the message followed by the full traceback, so
    prefer `output`, then the last log line that is not a traceback, then the first line of whatever is left.
    """
    output = action.get("output")
    if isinstance(output, str) and output.strip():
        return output.strip()
    messages = [m for m in _log_messages(action) if m.strip()]
    for message in reversed(messages):
        if not message.startswith("Traceback"):
            return message.strip()
    if messages:
        return messages[-1].strip().splitlines()[-1]
    return f"status {action.get('status')}"


def _mentions_lock(action: collections.abc.Mapping[str, typing.Any]) -> bool:
    output = action.get("output")
    texts = [output] if isinstance(output, str) else []
    texts.extend(_log_messages(action))
    return any("lock" in text.lower() for text in texts)


def _content_type(response: Response) -> str:
    return next((value for key, value in response.headers.items() if key.lower() == "content-type"), "").lower()


class V2Client(MicroscopeApi):
    """Drives an OpenFlexure Microscope Server v2 through a `Backend` (real HTTP or in-memory simulation)."""

    capabilities = frozenset(
        {
            Capability.DIGITAL_GAIN,
            Capability.EXPOSURE_MODE,
            Capability.WHITE_BALANCE_MODE,
            Capability.JPEG_QUALITY,
            Capability.CAPTURE_TAGS,
        }
    )

    def __init__(
        self,
        *,
        real_base_url: str,
        simulated: bool = True,
        request_timeout_s: float = 10.0,
        action_poll_interval_s: float = 0.25,
        simulated_backend: SimulatedBackend | None = None,
        http_backend_factory: HttpBackendFactory | None = None,
    ) -> None:
        self.real_base_url = real_base_url.rstrip("/")
        self._request_timeout_s = request_timeout_s
        self._action_poll_interval_s = action_poll_interval_s
        self._simulated_backend = simulated_backend or SimulatedBackend()
        self._http_backend_factory = http_backend_factory or _default_http_backend_factory
        self._simulated = simulated
        self._backend: Backend = (
            self._simulated_backend if simulated else self._http_backend_factory(self.real_base_url, request_timeout_s)
        )
        self._extension_links: dict[str, dict[str, str]] | None = None
        self._active_actions: dict[str, str] = {}

    @classmethod
    def from_config(cls, config: "OpenflexureMicroscopeConfig") -> "V2Client":
        """Create a client from the connector configuration."""
        return cls(
            real_base_url=f"http://{config.host}:{config.port}/api/v2",
            simulated=config.simulation_mode,
            request_timeout_s=config.request_timeout_s,
            action_poll_interval_s=config.action_poll_interval_s,
        )

    # -- lifecycle -----------------------------------------------------------------------------------------------

    @property
    def is_simulated(self) -> bool:
        """Whether the simulated backend is active."""
        return self._simulated

    @property
    def base_url(self) -> str:
        """Base URL of the active backend."""
        return getattr(self._backend, "base_url", self.real_base_url)

    @property
    def mjpeg_stream_url(self) -> str:
        """URL of the MJPEG preview stream on the active backend."""
        return f"{self.base_url}/streams/mjpeg"

    async def open(self) -> None:
        """In real mode, verify the server answers on `/api/v2`; in simulation this is a no-op."""
        if not self._simulated:
            await self._probe(self._backend)

    async def aclose(self) -> None:
        """Close the active backend (and the simulated one, which may hold background actions)."""
        await self._backend.aclose()
        if self._backend is not self._simulated_backend:
            await self._simulated_backend.aclose()

    async def start_simulation(self) -> None:
        """Switch to the in-memory simulation (refused while a server action is still running)."""
        if self._simulated:
            return
        self._refuse_while_busy("simulation mode")
        previous = self._backend
        self._backend = self._simulated_backend
        self._simulated = True
        self._extension_links = None
        await previous.aclose()

    async def start_real(self) -> None:
        """
        Switch to the real microscope.

        Raises:
          MicroscopeUnreachableError: If the server does not answer; the client then stays simulated.
        """
        if not self._simulated:
            return
        self._refuse_while_busy("real mode")
        candidate = self._http_backend_factory(self.real_base_url, self._request_timeout_s)
        try:
            await self._probe(candidate)
        except MicroscopeUnreachableError:
            await candidate.aclose()
            raise
        self._backend = candidate
        self._simulated = False
        self._extension_links = None

    def _refuse_while_busy(self, target: str) -> None:
        if self._active_actions:
            msg = (
                f"Cannot switch to {target} while {len(self._active_actions)} server action(s) are still running; "
                "wait for them to finish or stop them first."
            )
            raise OperationInProgressError(msg)

    async def ping(self) -> bool:
        """Whether the active backend answers the API root."""
        try:
            response = await self._backend.request("GET", "/")
        except MicroscopeUnreachableError:
            return False
        return response.ok

    async def _probe(self, backend: Backend) -> None:
        base_url = getattr(backend, "base_url", self.real_base_url)
        root = await backend.request("GET", "/")
        if root.status_code == 404:
            msg = (
                f"The server at {base_url} does not serve the OpenFlexure v2 API (/api/v2). "
                "It may be a v3 server or not an OpenFlexure microscope at all."
            )
            raise MicroscopeUnreachableError(msg)
        if not root.ok:
            msg = f"The OpenFlexure server at {base_url} answered unexpectedly ({_describe(root)})."
            raise MicroscopeUnreachableError(msg)
        configuration = await backend.request("GET", "/instrument/configuration")
        if not configuration.ok:
            msg = f"The OpenFlexure server at {base_url} did not report its configuration ({_describe(configuration)})."
            raise MicroscopeUnreachableError(msg)
        application = configuration.json().get("application", {})
        log.info(
            "Connected to %s %s at %s",
            application.get("name", "openflexure-microscope-server"),
            application.get("version", "?"),
            base_url,
        )

    async def get_server_info(self) -> ServerInfo:
        """Read configuration, settings and extensions into a `ServerInfo`."""
        configuration = (await self._get("/instrument/configuration", CameraUnavailableError)).json()
        settings = (await self._get("/instrument/settings", CameraUnavailableError)).json()
        extensions = await self._extensions()
        return ServerInfo(
            name=str(settings.get("name", "")),
            id=str(settings.get("id", "")),
            version=str(configuration.get("application", {}).get("version", "")),
            camera_type=CameraType(str(configuration.get("camera", {}).get("type", ""))),
            stage_type=StageType(str(configuration.get("stage", {}).get("type", ""))),
            extensions=sorted(extensions),
        )

    # -- stage ---------------------------------------------------------------------------------------------------

    async def get_position(self) -> StagePosition:
        """Read the stage position."""
        response = await self._get("/instrument/state/stage/position", StageUnavailableError)
        return StagePosition.from_mapping(response.json())

    async def move(
        self,
        x: int,
        y: int,
        z: int,
        *,
        absolute: bool,
        on_progress: ProgressCallback | None = None,
    ) -> StagePosition:
        """Move the stage through the `stage/move` action."""
        await self._require_stage()
        action = await self._run_action(
            "/actions/stage/move/",
            {"x": int(x), "y": int(y), "z": int(z), "absolute": bool(absolute)},
            on_progress,
            failure=MoveFailedError,
            busy=StageBusyError,
        )
        return StagePosition.from_mapping(action["output"])

    async def set_zero_position(self) -> StagePosition:
        """Reset the position readout to the origin through the `stage/zero` action."""
        await self._require_stage()
        action = await self._run_action("/actions/stage/zero/", {}, None, failure=MoveFailedError, busy=StageBusyError)
        output = action["output"] or {}
        return StagePosition.from_mapping(output.get("position", output))

    async def get_backlash(self) -> Backlash:
        """Read the stage backlash settings."""
        response = await self._get("/instrument/settings/stage/backlash", StageUnavailableError)
        return Backlash.from_mapping(response.json())

    async def abort_actions(self) -> int:
        """Cancel (DELETE) every action this client is still polling and return how many were cancelled."""
        cancelled = 0
        for action_id, href in list(self._active_actions.items()):
            try:
                response = await self._backend.request("DELETE", href)
            except MicroscopeUnreachableError:
                continue
            if response.ok:
                cancelled += 1
            log.info("Cancelled server action %s (HTTP %s)", action_id, response.status_code)
        return cancelled

    async def _require_stage(self) -> None:
        configuration = (await self._get("/instrument/configuration", StageUnavailableError)).json()
        stage_type = str(configuration.get("stage", {}).get("type", ""))
        if StageType(stage_type) is StageType.MISSING_STAGE:
            msg = (
                "The OpenFlexure server reports no stage (MissingStage); "
                "check the Sangaboard connection and restart the server."
            )
            raise StageUnavailableError(msg)

    # -- camera settings -----------------------------------------------------------------------------------------

    async def get_exposure_time_us(self) -> int:
        """Read `camera/picamera/shutter_speed`."""
        return int(await self._get_setting(f"{_PICAMERA}/shutter_speed"))

    async def set_exposure_time_us(self, exposure_time_us: int) -> None:
        """Write `camera/picamera/shutter_speed`."""
        await self._put_setting(f"{_PICAMERA}/shutter_speed", int(exposure_time_us))

    async def get_analog_gain(self) -> float:
        """Read `camera/picamera/analog_gain`."""
        return float(await self._get_setting(f"{_PICAMERA}/analog_gain"))

    async def set_analog_gain(self, gain: float) -> None:
        """Write `camera/picamera/analog_gain`."""
        await self._put_setting(f"{_PICAMERA}/analog_gain", float(gain))

    async def get_digital_gain(self) -> float:
        """Read `camera/picamera/digital_gain`."""
        return float(await self._get_setting(f"{_PICAMERA}/digital_gain"))

    async def set_digital_gain(self, gain: float) -> None:
        """Write `camera/picamera/digital_gain`."""
        await self._put_setting(f"{_PICAMERA}/digital_gain", float(gain))

    async def get_exposure_mode(self) -> ExposureMode:
        """Read `camera/picamera/exposure_mode`."""
        value = await self._get_setting(f"{_PICAMERA}/exposure_mode")
        return self._to_enum(ExposureMode, value, "exposure mode")

    async def set_exposure_mode(self, mode: ExposureMode) -> None:
        """Write `camera/picamera/exposure_mode`."""
        await self._put_setting(f"{_PICAMERA}/exposure_mode", mode.value)

    async def get_white_balance_mode(self) -> WhiteBalanceMode:
        """Read `camera/picamera/awb_mode`."""
        value = await self._get_setting(f"{_PICAMERA}/awb_mode")
        return self._to_enum(WhiteBalanceMode, value, "white balance mode")

    async def set_white_balance_mode(self, mode: WhiteBalanceMode) -> None:
        """Write `camera/picamera/awb_mode`."""
        await self._put_setting(f"{_PICAMERA}/awb_mode", mode.value)

    async def get_white_balance_gains(self) -> WhiteBalanceGains:
        """Read `camera/picamera/awb_gains` (`[red, blue]`)."""
        red, blue = await self._get_setting(f"{_PICAMERA}/awb_gains")
        return WhiteBalanceGains(red=float(red), blue=float(blue))

    async def set_white_balance_gains(self, red: float, blue: float) -> None:
        """Write `camera/picamera/awb_gains`; the server switches `awb_mode` to `off`."""
        await self._put_setting(f"{_PICAMERA}/awb_gains", [float(red), float(blue)])

    async def get_jpeg_quality(self) -> int:
        """Read `camera/jpeg_quality`."""
        return int(await self._get_setting("camera/jpeg_quality"))

    async def set_jpeg_quality(self, quality: int) -> None:
        """Write `camera/jpeg_quality`."""
        await self._put_setting("camera/jpeg_quality", int(quality))

    async def get_stream_resolution(self) -> Resolution:
        """Read `camera/stream_resolution`."""
        return Resolution.from_pair(await self._get_setting("camera/stream_resolution"))

    async def get_image_resolution(self) -> Resolution:
        """Read `camera/image_resolution`."""
        return Resolution.from_pair(await self._get_setting("camera/image_resolution"))

    async def _get_setting(self, path: str) -> typing.Any:  # noqa: ANN401 - settings are arbitrary JSON values
        """
        Read one nested setting.

        The v2 server cannot serve tuple-valued settings such as `camera/picamera/awb_gains` on their own route
        (Flask misreads the tuple, HTTP 500), so a server error falls back to reading the parent section.
        """
        response = await self._backend.request("GET", f"/instrument/settings/{path}")
        if response.status_code == 404:
            msg = f"The camera on this microscope does not expose the setting '{path}'."
            raise CameraSettingUnsupportedError(msg)
        if response.ok:
            return response.json()
        parent, _, leaf = path.rpartition("/")
        if response.status_code >= 500 and parent:
            section = await self._backend.request("GET", f"/instrument/settings/{parent}")
            if section.ok and isinstance(section.json(), dict) and leaf in section.json():
                return section.json()[leaf]
        msg = f"Reading setting '{path}' failed ({_describe(response)})."
        raise CameraSettingUnsupportedError(msg)

    async def _put_setting(self, path: str, value: object) -> None:
        """
        Write one nested setting.

        The v2 server only accepts a JSON object on settings PUTs, so the value is sent as a partial settings
        document to `/instrument/settings`. The key is read first so a missing setting is reported instead of
        being silently ignored.
        """
        await self._get_setting(path)
        document: object = value
        for key in reversed(path.split("/")):
            document = {key: document}
        response = await self._backend.request("PUT", "/instrument/settings", json=document)
        if not response.ok:
            msg = f"Writing setting '{path}' failed ({_describe(response)})."
            raise CameraSettingUnsupportedError(msg)

    @staticmethod
    def _to_enum(enum_type: type[typing.Any], value: object, what: str) -> typing.Any:  # noqa: ANN401
        try:
            return enum_type(str(value))
        except ValueError:
            msg = f"The server reports an unknown {what} {value!r}; supported values: {[m.value for m in enum_type]}."
            raise CameraSettingUnsupportedError(msg) from None

    # -- image acquisition ---------------------------------------------------------------------------------------

    async def grab_preview_frame(self) -> bytes:
        """Fetch `/streams/snapshot`."""
        await self._require_camera()
        response = await self._backend.request("GET", "/streams/snapshot", headers={"Accept": "image/jpeg"})
        if not response.ok:
            msg = f"The camera preview stream is not available ({_describe(response)})."
            raise CameraUnavailableError(msg)
        return response.content

    async def acquire_image(
        self,
        resolution: CaptureResolution,
        on_progress: ProgressCallback | None = None,
    ) -> bytes:
        """
        Capture an image into memory through `camera/ram-capture`.

        The server answers with the JPEG bytes directly when the capture finishes within its view timeout. A slow
        (full-resolution) capture answers with an Action instead, whose output cannot carry the image; then wait
        for it and fall back to a temporary stored capture that is downloaded and deleted.
        """
        await self._require_camera()
        response = await self._backend.request(
            "POST",
            "/actions/camera/ram-capture/",
            json={"use_video_port": resolution.use_video_port, "bayer": False},
            headers={"Accept": "image/jpeg"},
        )
        if not response.ok:
            msg = f"The in-memory capture failed ({_describe(response)})."
            raise CaptureFailedError(msg)
        if response.content.startswith(b"\xff\xd8"):
            if on_progress is not None:
                on_progress(1.0)
            return response.content
        if "json" in _content_type(response) or response.content.lstrip().startswith(b"{"):
            log.info("ram-capture answered with an Action; falling back to a temporary stored capture")
            action = response.json()
            await self._await_action(action, on_progress, failure=CaptureFailedError, busy=None)
            return await self._acquire_via_storage(resolution, on_progress)
        msg = "The server did not return a JPEG image for the in-memory capture."
        raise CaptureFailedError(msg)

    async def _acquire_via_storage(self, resolution: CaptureResolution, on_progress: ProgressCallback | None) -> bytes:
        body = {
            "use_video_port": resolution.use_video_port,
            "bayer": False,
            "tags": ["unitelabs-temporary"],
            "annotations": {},
            "temporary": True,
        }
        action = await self._run_action("/actions/camera/capture/", body, on_progress, failure=CaptureFailedError)
        capture_id = str((action.get("output") or {}).get("id", ""))
        if not capture_id:
            msg = "The temporary capture did not report an identifier, so its image cannot be downloaded."
            raise CaptureFailedError(msg)
        try:
            return await self.download_capture(capture_id)
        finally:
            with contextlib.suppress(Exception):
                await self.delete_capture(capture_id)

    async def capture_to_storage(
        self,
        filename: str,
        tags: collections.abc.Sequence[str],
        resolution: CaptureResolution,
        on_progress: ProgressCallback | None = None,
    ) -> CaptureInfo:
        """Run the `camera/capture` action and describe the stored capture."""
        await self._require_camera()
        body: dict[str, typing.Any] = {
            "use_video_port": resolution.use_video_port,
            "bayer": False,
            "tags": [str(tag) for tag in tags],
            "annotations": {},
            "temporary": False,
        }
        if filename:
            body["filename"] = filename
        action = await self._run_action("/actions/camera/capture/", body, on_progress, failure=CaptureFailedError)
        return CaptureInfo.from_mapping(action["output"])

    async def _require_camera(self) -> None:
        configuration = (await self._get("/instrument/configuration", CameraUnavailableError)).json()
        camera_type = str(configuration.get("camera", {}).get("type", ""))
        if CameraType(camera_type) is CameraType.MISSING_CAMERA:
            msg = (
                "The OpenFlexure server reports no camera (MissingCamera); "
                "check the camera cable and restart the server."
            )
            raise CameraUnavailableError(msg)

    # -- capture storage -----------------------------------------------------------------------------------------

    async def list_captures(self) -> list[CaptureInfo]:
        """List `/captures`."""
        response = await self._get("/captures", CaptureFailedError)
        return [CaptureInfo.from_mapping(item) for item in response.json()]

    async def download_capture(self, capture_id: str, *, thumbnail: bool = False) -> bytes:
        """Download a capture by looking up its file name, then fetching `/download/{name}`."""
        capture = await self._capture(capture_id)
        params = {"thumbnail": "true"} if thumbnail else None
        response = await self._backend.request(
            "GET",
            self._capture_path(capture_id, f"/download/{urllib.parse.quote(str(capture['name']), safe='')}"),
            params=params,
            headers={"Accept": "image/jpeg"},
        )
        if response.status_code == 404:
            raise CaptureNotFoundError(self._missing_capture(capture_id))
        if not response.ok:
            msg = f"Downloading capture {capture_id!r} failed ({_describe(response)})."
            raise CaptureFailedError(msg)
        return response.content

    async def read_capture_metadata(self, capture_id: str) -> bytes:
        """Return the capture's `metadata` as JSON bytes."""
        capture = await self._capture(capture_id)
        return _json.dumps(capture.get("metadata", {}), indent=2, sort_keys=True).encode("utf-8")

    async def set_capture_tags(self, capture_id: str, tags: collections.abc.Sequence[str]) -> list[str]:
        """Replace the capture's tags."""
        response = await self._backend.request(
            "PUT", self._capture_path(capture_id, "/tags"), json=[str(tag) for tag in tags]
        )
        if response.status_code == 404:
            raise CaptureNotFoundError(self._missing_capture(capture_id))
        if not response.ok:
            msg = f"Updating tags of capture {capture_id!r} failed ({_describe(response)})."
            raise CaptureFailedError(msg)
        return [str(tag) for tag in response.json()]

    async def delete_capture(self, capture_id: str) -> None:
        """Delete a stored capture."""
        response = await self._backend.request("DELETE", self._capture_path(capture_id))
        if response.status_code == 404:
            raise CaptureNotFoundError(self._missing_capture(capture_id))
        if not response.ok:
            msg = f"Deleting capture {capture_id!r} failed ({_describe(response)})."
            raise CaptureFailedError(msg)

    async def _capture(self, capture_id: str) -> dict[str, typing.Any]:
        response = await self._backend.request("GET", self._capture_path(capture_id))
        if response.status_code == 404:
            raise CaptureNotFoundError(self._missing_capture(capture_id))
        if not response.ok:
            msg = f"Reading capture {capture_id!r} failed ({_describe(response)})."
            raise CaptureFailedError(msg)
        return response.json()

    @staticmethod
    def _capture_path(capture_id: str, suffix: str = "") -> str:
        return f"/captures/{urllib.parse.quote(str(capture_id), safe='')}{suffix}"

    @staticmethod
    def _missing_capture(capture_id: str) -> str:
        return (
            f"No capture with id {capture_id!r} is stored on the microscope; read the Captures property for valid ids."
        )

    # -- autofocus -----------------------------------------------------------------------------------------------

    async def fast_autofocus(self, z_range: int, on_progress: ProgressCallback | None = None) -> StagePosition:
        """Run the extension's `fast_autofocus` action and return the resulting position."""
        await self._require_stage()
        href = await self._autofocus_link("fast_autofocus")
        await self._run_action(
            href, {"dz": int(z_range)}, on_progress, failure=AutofocusFailedError, busy=StageBusyError
        )
        return await self.get_position()

    async def autofocus_sweep(
        self,
        z_offsets: collections.abc.Sequence[int],
        on_progress: ProgressCallback | None = None,
    ) -> list[SharpnessSample]:
        """Run the extension's slow `autofocus` action and return its (positions, sharpnesses) output as samples."""
        await self._require_stage()
        href = await self._autofocus_link("autofocus")
        action = await self._run_action(
            href,
            {"dz": [int(offset) for offset in z_offsets]},
            on_progress,
            failure=AutofocusFailedError,
            busy=StageBusyError,
        )
        try:
            positions, sharpnesses = action["output"]
        except (TypeError, ValueError):
            msg = f"The autofocus action returned an unexpected result: {action.get('output')!r}."
            raise AutofocusFailedError(msg) from None
        return [SharpnessSample(z=int(z), sharpness=float(s)) for z, s in zip(positions, sharpnesses, strict=True)]

    async def measure_sharpness(self) -> float:
        """Call the extension's `measure_sharpness` view."""
        await self._require_camera()
        href = await self._autofocus_link("measure_sharpness")
        response = await self._backend.request("POST", href, json={})
        if not response.ok:
            msg = f"Measuring sharpness failed ({_describe(response)})."
            raise AutofocusFailedError(msg)
        return float(response.json()["sharpness"])

    async def _autofocus_link(self, name: str) -> str:
        links = (await self._extension_link_table()).get(AUTOFOCUS_EXTENSION)
        if not links or name not in links:
            msg = (
                f"The '{AUTOFOCUS_EXTENSION}' extension (link '{name}') is not installed on the OpenFlexure server; "
                "enable it on the microscope to use autofocus."
            )
            raise AutofocusUnavailableError(msg)
        return links[name]

    async def _extensions(self) -> list[str]:
        return list(await self._extension_link_table())

    async def _extension_link_table(self) -> dict[str, dict[str, str]]:
        if self._extension_links is None:
            response = await self._get("/extensions", AutofocusUnavailableError)
            table: dict[str, dict[str, str]] = {}
            for extension in response.json():
                links = extension.get("links") or {}
                table[str(extension.get("title", ""))] = {
                    str(name): str(link.get("href", "")) for name, link in links.items() if isinstance(link, dict)
                }
            self._extension_links = table
        return self._extension_links

    # -- plumbing ------------------------------------------------------------------------------------------------

    async def _get(self, path: str, failure: type[Exception]) -> Response:
        response = await self._backend.request("GET", path)
        if not response.ok:
            msg = f"GET {path} failed ({_describe(response)})."
            raise failure(msg)
        return response

    async def _run_action(
        self,
        path: str,
        body: object,
        on_progress: ProgressCallback | None,
        *,
        failure: type[Exception],
        busy: type[Exception] | None = None,
    ) -> dict[str, typing.Any]:
        """POST an action, poll it to completion (cancelling server-side on `CancelledError`) and return it."""
        response = await self._backend.request("POST", path, json=body)
        if not response.ok:
            msg = f"The server rejected the action POST {path} ({_describe(response)})."
            raise failure(msg)
        return await self._await_action(response.json(), on_progress, failure=failure, busy=busy)

    async def _await_action(
        self,
        action: dict[str, typing.Any],
        on_progress: ProgressCallback | None,
        *,
        failure: type[Exception],
        busy: type[Exception] | None,
    ) -> dict[str, typing.Any]:
        """Poll an Action until it leaves pending/running; cancel it server-side if this coroutine is cancelled."""
        self._report(on_progress, action)
        action_id = str(action.get("id", ""))
        href = str(action.get("links", {}).get("self", {}).get("href", ""))
        if action.get("status") in _RUNNING and action_id and href:
            self._active_actions[action_id] = href

        try:
            while action.get("status") in _RUNNING:
                await asyncio.sleep(self._action_poll_interval_s)
                poll = await self._backend.request("GET", href)
                if not poll.ok:
                    msg = f"Polling action {action_id} failed ({_describe(poll)})."
                    raise failure(msg)
                action = poll.json()
                self._report(on_progress, action)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._backend.request("DELETE", href)
            raise
        finally:
            self._active_actions.pop(action_id, None)

        status = action.get("status")
        if status == "completed":
            if on_progress is not None:
                on_progress(1.0)
            return action

        message = _error_message(action)
        if busy is not None and status == "error" and _mentions_lock(action):
            msg = f"The stage is busy with another operation: {message}"
            raise busy(msg)
        msg = f"Server action '{action.get('action', action_id)}' ended with status '{status}': {message}"
        raise failure(msg)

    @staticmethod
    def _report(on_progress: ProgressCallback | None, action: collections.abc.Mapping[str, typing.Any]) -> None:
        if on_progress is None:
            return
        progress = action.get("progress")
        if progress is None:
            on_progress(None)
        else:
            on_progress(max(0.0, min(1.0, float(progress) / 100.0)))
