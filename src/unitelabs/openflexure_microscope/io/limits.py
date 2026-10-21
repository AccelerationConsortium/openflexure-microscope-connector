"""
Software travel limits for the stage.

The OpenFlexure stage has no limit switches, so the connector can refuse moves that would leave a configured range
before any move is sent to the microscope. Limits are in motor steps, in the stage's current coordinate frame.
"""

import dataclasses

from .errors import TravelLimitExceededError
from .types import AxisLimit, Backlash, StageAxis, StagePosition

Range = tuple[int, int]


@dataclasses.dataclass(frozen=True)
class TravelLimits:
    """Allowed position range per axis; `None` means the axis is not limited."""

    x: Range | None = None
    y: Range | None = None
    z: Range | None = None

    def __post_init__(self) -> None:
        for axis, bounds in self._ranges():
            if bounds is not None and bounds[0] > bounds[1]:
                msg = f"Invalid {axis} travel limits {bounds}: the minimum must not exceed the maximum."
                raise ValueError(msg)

    @classmethod
    def from_bounds(
        cls,
        *,
        x_min: int | None,
        x_max: int | None,
        y_min: int | None,
        y_max: int | None,
        z_min: int | None,
        z_max: int | None,
    ) -> "TravelLimits":
        """
        Build limits from per-axis bounds, as the connector configuration validates them (both or neither per axis).

        An axis with neither bound is not limited. A half-set axis is not re-validated here, but it is never dropped
        silently either: comparing its bounds fails.
        """

        def pair(low: int | None, high: int | None) -> Range | None:
            return None if low is None and high is None else (low, high)  # type: ignore[return-value]

        return cls(x=pair(x_min, x_max), y=pair(y_min, y_max), z=pair(z_min, z_max))

    @property
    def enabled(self) -> bool:
        """Whether any axis is limited."""
        return any(bounds is not None for _, bounds in self._ranges())

    def as_axis_limits(self) -> list[AxisLimit]:
        """Return the limited axes as SiLA-friendly records; unlimited axes are left out."""
        return [
            AxisLimit(axis=StageAxis(axis), minimum=bounds[0], maximum=bounds[1])
            for axis, bounds in self._ranges()
            if bounds is not None
        ]

    def check_move(self, start: StagePosition, target: StagePosition, backlash: Backlash | None = None) -> None:
        """
        Refuse a move from `start` to `target` that would leave the limits.

        Only axes that move are checked, so an axis that is already outside its range does not block moves of the
        others. With backlash correction the stage first overshoots the target by the backlash on an axis that moves
        against the backlash's sign, then comes back; that overshoot must stay inside the limits too.

        Raises:
          TravelLimitExceededError: Naming every axis whose end point or overshoot would leave its range.
        """
        problems = []
        for axis, bounds in self._ranges():
            begin, end = getattr(start, axis), getattr(target, axis)
            if bounds is None or begin == end:
                continue
            low, high = bounds
            if not low <= end <= high:
                problems.append(f"{axis}={end} is outside [{low}, {high}]")
                continue
            correction = getattr(backlash, axis) if backlash is not None else 0
            overshoot = end - correction
            if correction * (end - begin) < 0 and not low <= overshoot <= high:
                problems.append(
                    f"{axis} would overshoot to {overshoot} while correcting {abs(correction)} steps of backlash, "
                    f"outside [{low}, {high}]"
                )
        if problems:
            raise TravelLimitExceededError(self._message("The move", problems))

    def check_z_range(self, z_low: int, z_high: int, *, backlash: int = 0) -> None:
        """
        Refuse a z sweep (e.g. an autofocus) that would leave the z limits.

        `backlash` is an overshoot below `z_low` that the sweep may make while correcting backlash.

        Raises:
          TravelLimitExceededError: If any part of the sweep is outside the z range.
        """
        if self.z is None:
            return
        low, high = self.z
        if z_low - backlash < low or z_high > high:
            overshoot = f" (including a {backlash}-step backlash overshoot)" if backlash else ""
            problem = f"z would sweep {z_low - backlash}..{z_high}{overshoot}, outside [{low}, {high}]"
            raise TravelLimitExceededError(self._message("The autofocus", [problem]))

    def _ranges(self) -> list[tuple[str, Range | None]]:
        return [("x", self.x), ("y", self.y), ("z", self.z)]

    @staticmethod
    def _message(what: str, problems: list[str]) -> str:
        return (
            f"{what} was refused before the stage moved: {'; '.join(problems)} steps. "
            "Choose a target inside the travel limits or change the stage_*_min/stage_*_max settings in config.json."
        )
