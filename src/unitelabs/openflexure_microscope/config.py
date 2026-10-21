import dataclasses
from importlib.metadata import version

from unitelabs.cdk import ConnectorBaseConfig, SiLAServerConfig
from unitelabs.cdk.config import ConfigurationError, validate_config, validate_field

__version__ = version("unitelabs-openflexure-microscope")

SUPPORTED_API_VERSIONS = ("v2",)


@dataclasses.dataclass
class OpenflexureMicroscopeConfig(ConnectorBaseConfig):
    """Configuration for the OpenFlexure Microscope connector."""

    simulation_mode: bool = True
    """
    Start without hardware, serving simulated responses.

    The SimulationController feature can switch between simulation and the real microscope at runtime.
    """

    api_version: str = "v2"
    """OpenFlexure Microscope Server API generation to talk to. Only "v2" (server 2.x, `/api/v2`) is implemented."""

    host: str = "microscope.local"
    """Hostname or IP address of the OpenFlexure Microscope server."""

    port: int = 5000
    """TCP port of the OpenFlexure Microscope server."""

    request_timeout_s: float = 10.0
    """Timeout in seconds for a single HTTP request to the server."""

    action_poll_interval_s: float = 0.25
    """Seconds between polls of a long-running server action (move, capture, autofocus)."""

    position_poll_interval_s: float = 0.5
    """Seconds between position reads while a client subscribes to the live stage position."""

    stage_x_min: int | None = None
    """Lowest x position moves may reach, in motor steps. Leave null for no limit; set together with stage_x_max."""

    stage_x_max: int | None = None
    """Highest x position moves may reach, in motor steps. Leave null for no limit; set together with stage_x_min."""

    stage_y_min: int | None = None
    """Lowest y position moves may reach, in motor steps. Leave null for no limit; set together with stage_y_max."""

    stage_y_max: int | None = None
    """Highest y position moves may reach, in motor steps. Leave null for no limit; set together with stage_y_min."""

    stage_z_min: int | None = None
    """Lowest z position moves and autofocus sweeps may reach, in motor steps. Set together with stage_z_max."""

    stage_z_max: int | None = None
    """Highest z position moves and autofocus sweeps may reach, in motor steps. Set together with stage_z_min."""

    export_folder: str = "~/Downloads/OpenFlexure"
    """Folder on the computer running the connector where CaptureStorageService.ExportCapture saves images (`~` is
    the home folder of the user running the connector)."""

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            name="OpenFlexure Microscope",
            type="Microscope",
            description="SiLA 2 connector for the OpenFlexure Microscope (server v2 HTTP API).",
            version=str(__version__),
            vendor_url="https://openflexure.org/",
        )
    )

    @validate_config()
    def _check_stage_limits(self) -> "OpenflexureMicroscopeConfig":
        for axis in "xyz":
            low, high = getattr(self, f"stage_{axis}_min"), getattr(self, f"stage_{axis}_max")
            if (low is None) != (high is None):
                msg = f"stage_{axis}_min and stage_{axis}_max must be set together, or both left null."
                raise ConfigurationError(msg)
            if low is not None and low > high:
                msg = f"stage_{axis}_min ({low}) must not be greater than stage_{axis}_max ({high})."
                raise ConfigurationError(msg)
        return self

    @validate_field("export_folder")
    @classmethod
    def _check_export_folder(cls, value: str) -> str:
        if not value.strip():
            msg = "export_folder must not be empty; give the folder where ExportCapture should save images."
            raise ConfigurationError(msg)
        return value

    @validate_field("api_version")
    @classmethod
    def _check_api_version(cls, value: str) -> str:
        if value not in SUPPORTED_API_VERSIONS:
            msg = f"api_version must be one of {SUPPORTED_API_VERSIONS}, got {value!r}."
            raise ConfigurationError(msg)
        return value
