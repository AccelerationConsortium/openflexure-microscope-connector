import collections.abc
from importlib.metadata import version

from unitelabs.cdk import Connector, get_logger
from unitelabs.cdk.features.core.lock_controller import LockController

from .config import OpenflexureMicroscopeConfig
from .features import (
    AutofocusController,
    CameraController,
    CaptureStorageService,
    ImageAcquisitionController,
    MicroscopeProvider,
    SimulationController,
    StageController,
)
from .io.api import build_api

__version__ = version("unitelabs-openflexure-microscope")

log = get_logger(__name__)


async def create_app(config: OpenflexureMicroscopeConfig) -> collections.abc.AsyncGenerator[Connector, None]:
    """
    Create the connector application.

    Builds the OpenFlexure API client selected by the configuration, verifies the microscope answers when running
    in real mode (no silent fallback to simulation), registers every feature and yields the connector. The API
    client is closed when the connector shuts down.
    """
    log.info(
        "Starting OpenFlexure Microscope connector v%s (simulation_mode=%s, api_version=%s, host=%s:%s)",
        __version__,
        config.simulation_mode,
        config.api_version,
        config.host,
        config.port,
    )

    api = build_api(config)
    try:
        if not config.simulation_mode:
            await api.open()

        app = Connector(config)
        app.register(LockController())
        app.register(SimulationController(api))
        app.register(StageController(api, position_poll_interval_s=config.position_poll_interval_s))
        app.register(ImageAcquisitionController(api))
        app.register(CaptureStorageService(api))
        app.register(CameraController(api))
        app.register(AutofocusController(api))
        app.register(MicroscopeProvider(api))

        yield app
    finally:
        await api.aclose()
