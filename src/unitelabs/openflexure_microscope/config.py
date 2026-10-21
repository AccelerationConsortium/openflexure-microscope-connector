import dataclasses
from importlib.metadata import version

from unitelabs.cdk import ConnectorBaseConfig, SiLAServerConfig
from unitelabs.cdk.config import ConfigurationError, validate_field

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

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            name="OpenFlexure Microscope",
            type="Microscope",
            description="SiLA 2 connector for the OpenFlexure Microscope (server v2 HTTP API).",
            version=str(__version__),
            vendor_url="https://openflexure.org/",
        )
    )

    @validate_field("api_version")
    @classmethod
    def _check_api_version(cls, value: str) -> str:
        if value not in SUPPORTED_API_VERSIONS:
            msg = f"api_version must be one of {SUPPORTED_API_VERSIONS}, got {value!r}."
            raise ConfigurationError(msg)
        return value
