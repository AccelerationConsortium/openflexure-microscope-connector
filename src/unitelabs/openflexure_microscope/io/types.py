"""
Version-neutral data types exchanged between the io layer and the SiLA features.

Dataclasses become SiLA Structures (field descriptions come from the `Attributes:` section), enums become SiLA
Strings constrained to the member names.
"""

import collections.abc
import dataclasses
import enum
import logging
import typing

from unitelabs.cdk.sila import constraints

from .units import PIXEL, STEP

log = logging.getLogger(__name__)


@dataclasses.dataclass
class StagePosition:
    """
    Position of the stage along its three axes.

    Attributes:
      X: Position along the x axis.
      Y: Position along the y axis.
      Z: Position along the z (focus) axis.
    """

    x: typing.Annotated[int, STEP]
    y: typing.Annotated[int, STEP]
    z: typing.Annotated[int, STEP]

    @classmethod
    def from_mapping(cls, data: collections.abc.Mapping[str, object]) -> "StagePosition":
        """Build a position from a server mapping with `x`, `y` and `z` keys (all required)."""
        return cls(x=int(data["x"]), y=int(data["y"]), z=int(data["z"]))  # type: ignore[call-overload]


@dataclasses.dataclass
class Backlash:
    """
    Backlash compensation applied by the stage on each axis.

    Attributes:
      X: Backlash along the x axis.
      Y: Backlash along the y axis.
      Z: Backlash along the z axis.
    """

    x: typing.Annotated[int, STEP]
    y: typing.Annotated[int, STEP]
    z: typing.Annotated[int, STEP]

    @classmethod
    def from_mapping(cls, data: collections.abc.Mapping[str, object]) -> "Backlash":
        """Build a backlash record from a server mapping with `x`, `y` and `z` keys."""
        return cls(x=int(data["x"]), y=int(data["y"]), z=int(data["z"]))  # type: ignore[call-overload]


@dataclasses.dataclass
class Resolution:
    """
    Image size in pixels.

    Attributes:
      Width: Image width.
      Height: Image height.
    """

    width: typing.Annotated[int, PIXEL]
    height: typing.Annotated[int, PIXEL]

    @classmethod
    def from_pair(cls, pair: collections.abc.Sequence[object]) -> "Resolution":
        """Build a resolution from a `[width, height]` pair as stored in the server settings."""
        width, height = pair
        return cls(width=int(width), height=int(height))  # type: ignore[call-overload]


@dataclasses.dataclass
class WhiteBalanceGains:
    """
    Manual white balance gains of the camera.

    Attributes:
      Red: Gain applied to the red channel.
      Blue: Gain applied to the blue channel.
    """

    red: float
    blue: float


class StageAxis(enum.Enum):
    """An axis of the stage."""

    X = "x"
    Y = "y"
    Z = "z"


@dataclasses.dataclass
class AxisLimit:
    """
    The positions the stage is allowed to move to along one axis.

    Attributes:
      Axis: The limited axis.
      Minimum: Lowest allowed position.
      Maximum: Highest allowed position.
    """

    axis: StageAxis
    minimum: typing.Annotated[int, STEP]
    maximum: typing.Annotated[int, STEP]


@dataclasses.dataclass
class Annotation:
    """
    A key/value note stored with a capture, e.g. a sample identifier or an experiment step.

    Attributes:
      Key: Name of the note; unique within one capture.
      Value: Text of the note.
    """

    key: typing.Annotated[str, constraints.MinimalLength(1)]
    value: str


@dataclasses.dataclass
class CaptureInfo:
    """
    A capture stored on the microscope.

    Attributes:
      Id: Unique identifier of the capture, used by the capture storage commands.
      Name: File name of the capture.
      Path: Path of the file on the microscope.
      Time: Capture time as reported by the server (ISO 8601).
      Format: Image format, e.g. `jpeg`.
      Tags: Tags attached to the capture.
      Annotations: Key/value notes stored with the capture.
    """

    id: str
    name: str
    path: str
    time: str
    format: str
    tags: list[str]
    annotations: list[Annotation] = dataclasses.field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: collections.abc.Mapping[str, object]) -> "CaptureInfo":
        """Build a record from a v2 `Capture` object; `id` and `name` are required, the rest default to empty."""
        tags = data.get("tags")
        annotations = data.get("annotations")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            path=str(data.get("path") or ""),
            time=str(data.get("time") or ""),
            format=str(data.get("format") or ""),
            tags=[str(tag) for tag in tags] if isinstance(tags, (list, tuple)) else [],
            annotations=(
                [Annotation(key=str(key), value=str(value)) for key, value in annotations.items()]
                if isinstance(annotations, collections.abc.Mapping)
                else []
            ),
        )


@dataclasses.dataclass
class SharpnessSample:
    """
    Sharpness measured at one z position during an autofocus sweep.

    Attributes:
      Z: Absolute z position at which the sharpness was measured.
      Sharpness: Sharpness metric reported by the server (higher is sharper).
    """

    z: typing.Annotated[int, STEP]
    sharpness: float


class StageType(enum.Enum):
    """Kind of stage reported by the server configuration."""

    SANGA_STAGE = "SangaStage"
    SANGA_DELTA_STAGE = "SangaDeltaStage"
    MISSING_STAGE = "MissingStage"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "StageType":
        log.warning("Unrecognized stage type %r; reporting UNKNOWN", value)
        return cls.UNKNOWN


class CameraType(enum.Enum):
    """Kind of camera reported by the server configuration."""

    PI_CAMERA_STREAMER = "PiCameraStreamer"
    MISSING_CAMERA = "MissingCamera"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "CameraType":
        log.warning("Unrecognized camera type %r; reporting UNKNOWN", value)
        return cls.UNKNOWN


class ExposureMode(enum.Enum):
    """Automatic exposure mode of the Raspberry Pi camera."""

    OFF = "off"
    AUTO = "auto"
    NIGHT = "night"
    NIGHT_PREVIEW = "nightpreview"
    BACKLIGHT = "backlight"
    SPOTLIGHT = "spotlight"
    SPORTS = "sports"
    SNOW = "snow"
    BEACH = "beach"
    VERY_LONG = "verylong"
    FIXED_FPS = "fixedfps"
    ANTISHAKE = "antishake"
    FIREWORKS = "fireworks"


class WhiteBalanceMode(enum.Enum):
    """Automatic white balance mode of the Raspberry Pi camera."""

    OFF = "off"
    AUTO = "auto"
    SUNLIGHT = "sunlight"
    CLOUDY = "cloudy"
    SHADE = "shade"
    TUNGSTEN = "tungsten"
    FLUORESCENT = "fluorescent"
    INCANDESCENT = "incandescent"
    FLASH = "flash"
    HORIZON = "horizon"


class CaptureResolution(enum.Enum):
    """Which camera path an image is taken from."""

    PREVIEW = "preview"
    """The preview stream resolution (fast, uses the video port)."""
    FULL = "full"
    """The full still-image resolution (slow, pauses the preview stream)."""

    @property
    def use_video_port(self) -> bool:
        """Whether the server should capture from the video (preview) port."""
        return self is CaptureResolution.PREVIEW


@dataclasses.dataclass
class ServerInfo:
    """
    Identity and build of the connected OpenFlexure server.

    Attributes:
      Name: Human-readable microscope name.
      Id: Unique microscope identifier.
      Version: OpenFlexure server version.
      CameraType: Kind of camera attached.
      StageType: Kind of stage attached.
      Extensions: Titles of the server extensions installed.
    """

    name: str
    id: str
    version: str
    camera_type: CameraType
    stage_type: StageType
    extensions: list[str]
