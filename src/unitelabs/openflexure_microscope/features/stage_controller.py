"""SiLA feature for moving the OpenFlexure Microscope stage and reading its position."""

import asyncio
import typing

from unitelabs.cdk import sila

from ..io.api import MicroscopeApi
from ..io.errors import MicroscopeUnreachableError, MoveFailedError, StageBusyError, StageUnavailableError
from ..io.types import Backlash, StagePosition, StageType
from ..io.units import STEP
from ._common import CATEGORY, ORIGINATOR
from ._progress import OperationProgress, run_observable

Steps = typing.Annotated[int, STEP]
_MOVE_ERRORS = (StageUnavailableError, StageBusyError, MoveFailedError, MicroscopeUnreachableError)
_READ_ERRORS = (StageUnavailableError, MicroscopeUnreachableError)


class StageController(sila.Feature):
    """
    Move the OpenFlexure Microscope stage and read its position.

    Positions are motor steps of the Sangaboard stepper drivers. The origin is wherever the stage was when the
    microscope server started or when `StageController.SetZeroPosition` was last called; the stage has no limit
    switches, so stay within the mechanical range of your microscope.

    Moves are observable commands. `StageController.Stop` cancels a running move on the microscope and, because
    autofocus also drives the stage, a running autofocus routine as well.
    """

    def __init__(self, api: MicroscopeApi, *, position_poll_interval_s: float = 0.5) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api
        self._position_poll_interval_s = position_poll_interval_s

    @sila.UnobservableProperty(errors=_READ_ERRORS)
    async def get_position(self) -> StagePosition:
        """The current stage position."""
        return await self._api.get_position()

    @sila.ObservableProperty(name="Live Position", errors=_READ_ERRORS)
    async def subscribe_position(self) -> sila.Stream[StagePosition]:
        """The stage position, sent whenever it changes."""
        previous: StagePosition | None = None
        while True:
            current = await self._api.get_position()
            if current != previous:
                yield current
                previous = current
            await asyncio.sleep(self._position_poll_interval_s)

    @sila.UnobservableProperty(errors=[MicroscopeUnreachableError])
    async def get_stage_type(self) -> StageType:
        """The kind of stage attached to the microscope."""
        return (await self._api.get_server_info()).stage_type

    @sila.UnobservableProperty(errors=_READ_ERRORS)
    async def get_backlash(self) -> Backlash:
        """The backlash compensation the stage applies on each axis."""
        return await self._api.get_backlash()

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_MOVE_ERRORS)
    async def move_to(
        self,
        x: Steps,
        y: Steps,
        z: Steps,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> StagePosition:
        """
        Move the stage to an absolute position.

        Args:
          X: Target position along the x axis.
          Y: Target position along the y axis.
          Z: Target position along the z (focus) axis.

        Yields:
          Progress: Lifecycle updates of the move.

        Returns:
          Position: The stage position after the move.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Moving the stage to ({x}, {y}, {z}).",
            complete="Stage move finished.",
            cancelled="Stage move cancelled.",
            action=lambda on_progress: self._api.move(x, y, z, absolute=True, on_progress=on_progress),
        )

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_MOVE_ERRORS)
    async def move_by(
        self,
        x: Steps,
        y: Steps,
        z: Steps,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> StagePosition:
        """
        Move the stage relative to its current position.

        Args:
          X: Distance to move along the x axis.
          Y: Distance to move along the y axis.
          Z: Distance to move along the z (focus) axis.

        Yields:
          Progress: Lifecycle updates of the move.

        Returns:
          Position: The stage position after the move.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Moving the stage by ({x}, {y}, {z}).",
            complete="Stage move finished.",
            cancelled="Stage move cancelled.",
            action=lambda on_progress: self._api.move(x, y, z, absolute=False, on_progress=on_progress),
        )

    @sila.UnobservableCommand(errors=_MOVE_ERRORS)
    async def set_zero_position(self) -> StagePosition:
        """
        Make the current stage position the origin without moving.

        Returns:
          Position: The stage position after zeroing, always (0, 0, 0).
        """
        return await self._api.set_zero_position()

    @sila.UnobservableCommand(errors=[MicroscopeUnreachableError])
    async def stop(self) -> int:
        """
        Stop the stage by cancelling every running move or autofocus action on the microscope.

        The cancelled commands finish with a `MoveFailedError` or `AutofocusFailedError` that reports the
        cancellation.

        Returns:
          StoppedOperations: How many running operations were cancelled.
        """
        return await self._api.abort_actions()
