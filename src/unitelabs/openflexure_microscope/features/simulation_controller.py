"""SiLA core feature switching the connector between the simulated and the real microscope."""

from unitelabs.cdk import sila
from unitelabs.cdk.features.core.simulation_controller import (
    SimulationControllerBase,
    StartRealModeFailed,
    StartSimulationModeFailed,
)

from ..io.api import MicroscopeApi
from ..io.errors import MicroscopeUnreachableError, OperationInProgressError


class SimulationController(SimulationControllerBase):
    """
    Switch the connector between the simulated microscope and the real OpenFlexure server.

    In simulation mode every feature is served by an in-memory stand-in of the OpenFlexure server; switching to
    real mode connects to the configured host and port and fails (staying simulated) if the server does not answer.
    """

    def __init__(self, api: MicroscopeApi) -> None:
        super().__init__()
        self._api = api

    @sila.UnobservableCommand(errors=[StartSimulationModeFailed])
    async def start_simulation_mode(self) -> None:
        """
        Set the SiLA Server to run in Simulation Mode, i.e. all following commands are executed in simulation mode.

        The simulated microscope answers every command with plausible data and never touches hardware. The
        simulation mode can be stopped by issuing the 'Start Real Mode' command.

        Raises:
          StartSimulationModeFailed: If the server cannot change to Simulation Mode.
        """
        try:
            await self._api.start_simulation()
        except OperationInProgressError as exc:
            raise StartSimulationModeFailed(str(exc)) from exc

    @sila.UnobservableCommand(errors=[StartRealModeFailed])
    async def start_real_mode(self) -> None:
        """
        Set the SiLA Server to run in real mode, talking to the OpenFlexure Microscope at the configured host.

        If the microscope does not answer, the server stays in Simulation Mode. The Real Mode can be stopped by
        issuing the 'Start Simulation Mode' command.

        Raises:
          StartRealModeFailed: If the OpenFlexure Microscope cannot be reached or does not serve the expected API.
        """
        try:
            await self._api.start_real()
        except (MicroscopeUnreachableError, OperationInProgressError) as exc:
            raise StartRealModeFailed(str(exc)) from exc

    @sila.UnobservableProperty(name="SimulationMode")
    async def simulation_mode(self) -> bool:
        """Whether or not the SiLA Server is in Simulation Mode."""
        return self._api.is_simulated
