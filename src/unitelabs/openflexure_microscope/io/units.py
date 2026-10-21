"""
SiLA unit constraints shared by the io types and the features.

Units live here (never in identifiers or docstrings) so every numeric value on the SiLA interface carries the
same definition.
"""

from unitelabs.cdk.sila import constraints

STEP = constraints.Unit(
    label="step",
    components=[constraints.UnitComponent(constraints.SIUnit.DIMENSIONLESS)],
)
"""One motor step of the OpenFlexure stage. The server reports and accepts positions in steps."""

MICROSECOND = constraints.Unit(
    label="us",
    components=[constraints.UnitComponent(constraints.SIUnit.SECOND)],
    factor=1e-6,
)
"""Microseconds, as used by the camera exposure (shutter) time."""

PERCENT = constraints.Unit(
    label="%",
    components=[constraints.UnitComponent(constraints.SIUnit.DIMENSIONLESS)],
    factor=0.01,
)
"""Percent of a dimensionless quantity, e.g. JPEG quality."""

PIXEL = constraints.Unit(
    label="px",
    components=[constraints.UnitComponent(constraints.SIUnit.DIMENSIONLESS)],
)
"""Image pixels."""
