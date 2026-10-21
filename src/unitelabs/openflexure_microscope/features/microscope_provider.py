"""SiLA feature describing the connected OpenFlexure Microscope server."""

import asyncio

from unitelabs.cdk import sila

from ..io.api import MicroscopeApi
from ..io.errors import MicroscopeUnreachableError
from ..io.types import CameraType
from ._common import CATEGORY, ORIGINATOR


class MicroscopeProvider(sila.Feature):
    """
    Identify the connected OpenFlexure Microscope and report whether it is reachable.

    The values come from the microscope server's configuration and settings and reflect the simulated microscope
    while the connector runs in simulation mode.
    """

    def __init__(self, api: MicroscopeApi, *, reachable_poll_interval_s: float = 5.0) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api
        self._reachable_poll_interval_s = reachable_poll_interval_s

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_microscope_name(self) -> str:
        """The human-readable name configured on the microscope."""
        return (await self._api.get_server_info()).name

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_microscope_id(self) -> str:
        """The unique identifier of the microscope."""
        return (await self._api.get_server_info()).id

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_server_version(self) -> str:
        """The version of the OpenFlexure Microscope server software."""
        return (await self._api.get_server_info()).version

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_camera_type(self) -> CameraType:
        """The kind of camera attached to the microscope."""
        return (await self._api.get_server_info()).camera_type

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_installed_extensions(self) -> list[str]:
        """The server extensions installed on the microscope."""
        return (await self._api.get_server_info()).extensions

    @sila.UnobservableProperty(name="API Base URL")
    async def get_api_base_url(self) -> str:
        """The base URL of the microscope HTTP API the connector talks to."""
        return self._api.base_url

    @sila.ObservableProperty()
    async def subscribe_reachable(self) -> sila.Stream[bool]:
        """Whether the microscope server answers, sent whenever it changes."""
        previous: bool | None = None
        while True:
            current = await self._api.ping()
            if current != previous:
                yield current
                previous = current
            await asyncio.sleep(self._reachable_poll_interval_s)
