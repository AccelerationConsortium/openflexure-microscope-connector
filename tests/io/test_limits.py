import pytest

from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.limits import TravelLimits


def test_no_limits_by_default():
    limits = TravelLimits()
    assert limits.enabled is False
    assert limits.as_axis_limits() == []
    limits.check_move(types.StagePosition(0, 0, 0), types.StagePosition(10**9, -(10**9), 10**9))
    limits.check_z_range(-(10**9), 10**9)


def test_axis_limits_report_only_bounded_axes():
    limits = TravelLimits(x=(-100, 100), z=(0, 50))
    assert limits.enabled is True
    assert limits.as_axis_limits() == [
        types.AxisLimit(axis=types.StageAxis.X, minimum=-100, maximum=100),
        types.AxisLimit(axis=types.StageAxis.Z, minimum=0, maximum=50),
    ]


def test_position_inside_limits_is_accepted_including_the_bounds():
    limits = TravelLimits(x=(-100, 100), y=(-100, 100), z=(0, 50))
    start = types.StagePosition(1, 1, 1)
    limits.check_move(start, types.StagePosition(100, -100, 0))
    limits.check_move(start, types.StagePosition(0, 0, 50))


def test_position_outside_limits_is_refused_naming_every_violation():
    limits = TravelLimits(x=(-100, 100), z=(0, 50))
    with pytest.raises(errors.TravelLimitExceededError) as exc_info:
        limits.check_move(types.StagePosition(0, 0, 0), types.StagePosition(101, 10**6, -1))
    message = str(exc_info.value)
    assert "x=101" in message and "[-100, 100]" in message
    assert "z=-1" in message and "[0, 50]" in message
    assert "y=" not in message  # y is not limited


def test_z_range_check():
    limits = TravelLimits(z=(0, 50))
    limits.check_z_range(0, 50)
    with pytest.raises(errors.TravelLimitExceededError, match="z"):
        limits.check_z_range(-10, 20)
    with pytest.raises(errors.TravelLimitExceededError, match="z"):
        limits.check_z_range(10, 51)
    TravelLimits(x=(0, 1)).check_z_range(-(10**6), 10**6)  # z unlimited


def test_from_bounds_builds_axes_that_have_both_bounds():
    limits = TravelLimits.from_bounds(x_min=-5, x_max=5, y_min=None, y_max=None, z_min=0, z_max=9)
    assert limits == TravelLimits(x=(-5, 5), z=(0, 9))


def test_invalid_ranges_are_rejected():
    with pytest.raises(ValueError, match="x"):
        TravelLimits(x=(5, -5))


def test_axes_that_do_not_move_are_not_checked():
    limits = TravelLimits(x=(100, 900), z=(0, 1000))
    start = types.StagePosition(0, 0, 500)  # x is outside its range, e.g. after the Sangaboard was power cycled
    limits.check_move(start, types.StagePosition(0, 0, 510))  # moving only z is allowed
    with pytest.raises(errors.TravelLimitExceededError, match="x=50"):
        limits.check_move(start, types.StagePosition(50, 0, 500))  # a moving axis must land inside its range
    limits.check_move(start, types.StagePosition(100, 0, 500))  # moving x back inside is allowed


def test_backlash_overshoot_must_stay_inside_the_limits():
    limits = TravelLimits(z=(0, 1000))
    positive = types.Backlash(x=0, y=0, z=100)
    # Moving down against a positive backlash overshoots 100 steps below the target before coming back.
    with pytest.raises(errors.TravelLimitExceededError, match="backlash"):
        limits.check_move(types.StagePosition(0, 0, 500), types.StagePosition(0, 0, 50), positive)
    limits.check_move(types.StagePosition(0, 0, 500), types.StagePosition(0, 0, 100), positive)  # reaches exactly 0
    limits.check_move(types.StagePosition(0, 0, 0), types.StagePosition(0, 0, 1000), positive)  # moving up: none
    negative = types.Backlash(x=0, y=0, z=-100)
    with pytest.raises(errors.TravelLimitExceededError, match="backlash"):
        limits.check_move(types.StagePosition(0, 0, 500), types.StagePosition(0, 0, 950), negative)
    limits.check_move(types.StagePosition(0, 0, 500), types.StagePosition(0, 0, 950), None)  # no backlash known


def test_sweep_range_can_include_a_backlash_overshoot_below():
    limits = TravelLimits(z=(0, 1000))
    limits.check_z_range(256, 600, backlash=256)
    with pytest.raises(errors.TravelLimitExceededError, match="256"):
        limits.check_z_range(255, 600, backlash=256)


def test_a_half_set_axis_is_never_silently_unlimited():
    # The connector configuration rejects it; built in code, it still fails loudly instead of dropping the limit.
    with pytest.raises(TypeError):
        TravelLimits.from_bounds(x_min=5, x_max=None, y_min=None, y_max=None, z_min=None, z_max=None)
