from unitelabs.cdk.sila import constraints

from unitelabs.openflexure_microscope.io import units


def test_units_are_sila_unit_constraints():
    for unit in (units.STEP, units.MICROSECOND, units.PERCENT, units.PIXEL):
        assert isinstance(unit, constraints.Unit)


def test_microsecond_scales_to_seconds():
    assert units.MICROSECOND.label == "us"
    assert units.MICROSECOND.factor == 1e-6
    assert units.MICROSECOND.components[0].unit in (constraints.SIUnit.SECOND, "Second")


def test_dimensionless_units():
    assert units.STEP.label == "step"
    assert units.PIXEL.label == "px"
    assert units.PERCENT.label == "%"
    assert units.PERCENT.factor == 0.01
