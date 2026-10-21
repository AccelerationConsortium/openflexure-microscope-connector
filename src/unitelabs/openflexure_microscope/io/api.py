"""
Version-neutral contract between the SiLA features and an OpenFlexure server implementation.

Features only import this module (and `types`/`errors`). The OpenFlexure v2 HTTP API is implemented in `io.v2`;
a future v3 implementation plugs in here without touching the features.
"""

import abc
import collections.abc
import enum
import typing

from unitelabs.cdk.config import ConfigurationError

from .limits import TravelLimits

from .types import (
    Backlash,
    CaptureInfo,
    CaptureResolution,
    ExposureMode,
    Resolution,
    ServerInfo,
    SharpnessSample,
    StagePosition,
    WhiteBalanceGains,
    WhiteBalanceMode,
)

if typing.TYPE_CHECKING:
    from ..config import OpenflexureMicroscopeConfig

ProgressCallback = collections.abc.Callable[[float | None], None]
"""Receives the fraction completed (0..1) of a long-running server action, or `None` when unknown."""

SUPPORTED_API_VERSIONS = ("v2",)


class Capability(enum.Enum):
    """Optional device functions an implementation may or may not provide."""

    DIGITAL_GAIN = "digital_gain"
    EXPOSURE_MODE = "exposure_mode"
    WHITE_BALANCE_MODE = "white_balance_mode"
    JPEG_QUALITY = "jpeg_quality"
    CAPTURE_TAGS = "capture_tags"
    ILLUMINATION = "illumination"


class MicroscopeApi(abc.ABC):
    """
    Everything the SiLA features need from an OpenFlexure microscope, independent of the server API version.

    Positions are motor steps, exposure times are microseconds, images are JPEG bytes. Long-running calls accept
    an `on_progress` callback and are cancelled with `asyncio.CancelledError`, which implementations translate into
    a server-side cancellation where the server supports it. All failures are raised as the classes in `io.errors`.
    """

    capabilities: frozenset[Capability] = frozenset()
    travel_limits: TravelLimits = TravelLimits()
    """Stage travel limits enforced by `move`, `fast_autofocus` and `autofocus_sweep` (none by default)."""

    def supports(self, capability: Capability) -> bool:
        """Whether this implementation provides the given optional capability."""
        return capability in self.capabilities

    # -- lifecycle -----------------------------------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def is_simulated(self) -> bool:
        """Whether calls are served by the simulated backend instead of a real microscope."""

    @property
    @abc.abstractmethod
    def base_url(self) -> str:
        """Base URL of the microscope API, e.g. `http://microscope.local:5000/api/v2`."""

    @property
    @abc.abstractmethod
    def mjpeg_stream_url(self) -> str:
        """URL of the live MJPEG preview stream."""

    @abc.abstractmethod
    async def open(self) -> None:
        """Connect to the microscope (in real mode: verify the server answers and speaks the expected API)."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Release network resources."""

    @abc.abstractmethod
    async def start_simulation(self) -> None:
        """Switch to the simulated backend; raises `OperationInProgressError` while a server action is running."""

    @abc.abstractmethod
    async def start_real(self) -> None:
        """
        Switch to the real microscope.

        Raises `MicroscopeUnreachableError` (and stays simulated) if the server does not answer, and
        `OperationInProgressError` while a server action is still running.
        """

    @abc.abstractmethod
    async def ping(self) -> bool:
        """Whether the server currently answers."""

    @abc.abstractmethod
    async def get_server_info(self) -> ServerInfo:
        """Read the server identity, version, hardware kinds and installed extensions."""

    # -- stage ---------------------------------------------------------------------------------------------------

    @abc.abstractmethod
    async def get_position(self) -> StagePosition:
        """Read the current stage position."""

    @abc.abstractmethod
    async def move(
        self,
        x: int,
        y: int,
        z: int,
        *,
        absolute: bool,
        on_progress: ProgressCallback | None = None,
    ) -> StagePosition:
        """Move the stage to (`absolute`) or by (`relative`) the given steps and return the resulting position."""

    @abc.abstractmethod
    async def set_zero_position(self) -> StagePosition:
        """Make the current position read as (0, 0, 0) without moving."""

    @abc.abstractmethod
    async def get_backlash(self) -> Backlash:
        """Read the configured backlash compensation."""

    @abc.abstractmethod
    async def abort_actions(self) -> int:
        """Cancel every long-running server action started by this connector that is still running; return how many."""

    # -- camera settings -----------------------------------------------------------------------------------------

    @abc.abstractmethod
    async def get_exposure_time_us(self) -> int:
        """Read the exposure (shutter) time in microseconds."""

    @abc.abstractmethod
    async def set_exposure_time_us(self, exposure_time_us: int) -> None:
        """Set the exposure (shutter) time in microseconds."""

    @abc.abstractmethod
    async def get_analog_gain(self) -> float:
        """Read the analog gain."""

    @abc.abstractmethod
    async def set_analog_gain(self, gain: float) -> None:
        """Set the analog gain."""

    @abc.abstractmethod
    async def get_digital_gain(self) -> float:
        """Read the digital gain (`Capability.DIGITAL_GAIN`)."""

    @abc.abstractmethod
    async def set_digital_gain(self, gain: float) -> None:
        """Set the digital gain (`Capability.DIGITAL_GAIN`)."""

    @abc.abstractmethod
    async def get_exposure_mode(self) -> ExposureMode:
        """Read the automatic exposure mode (`Capability.EXPOSURE_MODE`)."""

    @abc.abstractmethod
    async def set_exposure_mode(self, mode: ExposureMode) -> None:
        """Set the automatic exposure mode (`Capability.EXPOSURE_MODE`)."""

    @abc.abstractmethod
    async def get_white_balance_mode(self) -> WhiteBalanceMode:
        """Read the automatic white balance mode (`Capability.WHITE_BALANCE_MODE`)."""

    @abc.abstractmethod
    async def set_white_balance_mode(self, mode: WhiteBalanceMode) -> None:
        """Set the automatic white balance mode (`Capability.WHITE_BALANCE_MODE`)."""

    @abc.abstractmethod
    async def get_white_balance_gains(self) -> WhiteBalanceGains:
        """Read the manual white balance gains."""

    @abc.abstractmethod
    async def set_white_balance_gains(self, red: float, blue: float) -> None:
        """Set the manual white balance gains (switches automatic white balance off)."""

    @abc.abstractmethod
    async def get_jpeg_quality(self) -> int:
        """Read the JPEG quality in percent (`Capability.JPEG_QUALITY`)."""

    @abc.abstractmethod
    async def set_jpeg_quality(self, quality: int) -> None:
        """Set the JPEG quality in percent (`Capability.JPEG_QUALITY`)."""

    @abc.abstractmethod
    async def get_stream_resolution(self) -> Resolution:
        """Read the preview stream resolution."""

    @abc.abstractmethod
    async def get_image_resolution(self) -> Resolution:
        """Read the full still-image resolution."""

    # -- image acquisition ---------------------------------------------------------------------------------------

    @abc.abstractmethod
    async def grab_preview_frame(self) -> bytes:
        """Return the next frame of the preview stream as JPEG bytes."""

    @abc.abstractmethod
    async def acquire_image(
        self,
        resolution: CaptureResolution,
        on_progress: ProgressCallback | None = None,
    ) -> bytes:
        """Capture an image into memory and return it as JPEG bytes."""

    @abc.abstractmethod
    async def capture_to_storage(
        self,
        filename: str,
        tags: collections.abc.Sequence[str],
        resolution: CaptureResolution,
        on_progress: ProgressCallback | None = None,
        *,
        annotations: collections.abc.Mapping[str, str] | None = None,
    ) -> CaptureInfo:
        """Capture an image into the microscope's storage, with optional key/value annotations, and describe it."""

    # -- capture storage -----------------------------------------------------------------------------------------

    @abc.abstractmethod
    async def list_captures(self) -> list[CaptureInfo]:
        """List the captures stored on the microscope."""

    @abc.abstractmethod
    async def get_capture(self, capture_id: str) -> CaptureInfo:
        """Describe one stored capture; raises `CaptureNotFoundError` if it does not exist."""

    @abc.abstractmethod
    async def download_capture(self, capture_id: str, *, thumbnail: bool = False) -> bytes:
        """Download a stored capture (or its thumbnail) as JPEG bytes."""

    @abc.abstractmethod
    async def read_capture_metadata(self, capture_id: str) -> bytes:
        """Return the stored capture's metadata as UTF-8 JSON bytes."""

    @abc.abstractmethod
    async def set_capture_tags(self, capture_id: str, tags: collections.abc.Sequence[str]) -> list[str]:
        """Replace the tags of a stored capture and return the new tags (`Capability.CAPTURE_TAGS`)."""

    @abc.abstractmethod
    async def delete_capture(self, capture_id: str) -> None:
        """Delete a stored capture."""

    # -- autofocus -----------------------------------------------------------------------------------------------

    @abc.abstractmethod
    async def fast_autofocus(self, z_range: int, on_progress: ProgressCallback | None = None) -> StagePosition:
        """Run the fast (JPEG-size based) autofocus over `z_range` steps and return the resulting position."""

    @abc.abstractmethod
    async def autofocus_sweep(
        self,
        z_offsets: collections.abc.Sequence[int],
        on_progress: ProgressCallback | None = None,
    ) -> list[SharpnessSample]:
        """Measure sharpness at each relative z offset, move to the sharpest, and return the samples."""

    @abc.abstractmethod
    async def measure_sharpness(self) -> float:
        """Measure the sharpness of the current preview image."""


def build_api(config: "OpenflexureMicroscopeConfig") -> MicroscopeApi:
    """
    Create the API implementation selected by `config.api_version`.

    Raises:
      ConfigurationError: If the configured API version is not implemented.
    """
    api_version = getattr(config, "api_version", None)
    if api_version == "v2":
        from .v2.client import V2Client

        return V2Client.from_config(config)

    msg = f"Unsupported api_version {api_version!r}; implemented versions: {', '.join(SUPPORTED_API_VERSIONS)}."
    raise ConfigurationError(msg)
