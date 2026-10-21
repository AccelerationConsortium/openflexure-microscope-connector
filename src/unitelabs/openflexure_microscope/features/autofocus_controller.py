"""SiLA feature for the autofocus routines of the OpenFlexure Microscope."""

import typing

from unitelabs.cdk import sila
from unitelabs.cdk.sila import constraints

from ..io.api import MicroscopeApi
from ..io.errors import (
    AutofocusFailedError,
    AutofocusUnavailableError,
    CameraUnavailableError,
    MicroscopeUnreachableError,
    StageBusyError,
    StageUnavailableError,
)
from ..io.types import SharpnessSample, StagePosition
from ..io.units import STEP
from ._common import CATEGORY, ORIGINATOR
from ._progress import OperationProgress, run_observable

ZRange = typing.Annotated[int, STEP, constraints.MinimalInclusive(1)]
ZOffsets = typing.Annotated[list[typing.Annotated[int, STEP]], constraints.MinimalElementCount(2)]
_AUTOFOCUS_ERRORS = (
    AutofocusUnavailableError,
    AutofocusFailedError,
    StageBusyError,
    StageUnavailableError,
    MicroscopeUnreachableError,
)


class AutofocusController(sila.Feature):
    """
    Focus the OpenFlexure Microscope automatically with the server's autofocus extension.

    `RunFastAutofocus` sweeps the z axis while watching the size of the preview JPEG frames and moves to the
    sharpest position; it takes a few seconds. `RunAutofocus` steps through explicit z offsets, measures a sharpness
    metric at each and moves to the sharpest; it is slower but returns the measurements. Both drive the stage and
    can be stopped with `Stop`.
    """

    def __init__(self, api: MicroscopeApi) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_AUTOFOCUS_ERRORS)
    async def run_fast_autofocus(
        self,
        z_range: ZRange,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> StagePosition:
        """
        Run the fast autofocus routine and move to the sharpest position.

        Args:
          ZRange: Total z travel to search, centred on the current position.

        Yields:
          Progress: Lifecycle updates of the routine.

        Returns:
          Position: The stage position at the best focus.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Running the fast autofocus over {z_range} steps.",
            complete="Fast autofocus finished.",
            cancelled="Fast autofocus cancelled.",
            action=lambda on_progress: self._api.fast_autofocus(z_range, on_progress=on_progress),
        )

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_AUTOFOCUS_ERRORS)
    async def run_autofocus(
        self,
        z_offsets: ZOffsets,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> list[SharpnessSample]:
        """
        Measure the sharpness at a series of z offsets and move to the sharpest.

        Args:
          ZOffsets: Ascending z offsets relative to the current position at which to measure the sharpness.

        Yields:
          Progress: Lifecycle updates of the routine.

        Returns:
          Samples: The absolute z position and sharpness of every measurement.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Running the autofocus over {len(z_offsets)} z positions.",
            complete="Autofocus finished.",
            cancelled="Autofocus cancelled.",
            action=lambda on_progress: self._api.autofocus_sweep(z_offsets, on_progress=on_progress),
        )

    @sila.UnobservableCommand(
        errors=[AutofocusUnavailableError, AutofocusFailedError, CameraUnavailableError, MicroscopeUnreachableError]
    )
    async def measure_sharpness(self) -> float:
        """
        Measure the sharpness of the current preview image.

        Returns:
          Sharpness: The sharpness metric of the current image; higher is sharper.
        """
        return await self._api.measure_sharpness()

    @sila.UnobservableCommand(errors=[MicroscopeUnreachableError])
    async def stop(self) -> int:
        """
        Stop a running autofocus routine by cancelling it on the microscope.

        The cancelled routine finishes with an `AutofocusFailedError` that reports the cancellation. Because the
        routines drive the stage, this also stops a running `StageController` move.

        Returns:
          StoppedOperations: How many running operations were cancelled.
        """
        return await self._api.abort_actions()
