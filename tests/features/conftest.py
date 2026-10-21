from unittest.mock import Mock

import pytest

from unitelabs.openflexure_microscope.io.v2.client import V2Client
from unitelabs.openflexure_microscope.io.v2.simulation import SimulatedBackend

REAL_URL = "http://microscope.local:5000/api/v2"


def make_api(backend: SimulatedBackend | None = None) -> V2Client:
    return V2Client(
        real_base_url=REAL_URL,
        simulated=True,
        simulated_backend=backend or SimulatedBackend(),
        action_poll_interval_s=0.005,
    )


@pytest.fixture
def backend() -> SimulatedBackend:
    return SimulatedBackend()


@pytest.fixture
def api(backend) -> V2Client:
    return make_api(backend)


@pytest.fixture
def status() -> Mock:
    return Mock(name="status")


@pytest.fixture
def intermediate() -> Mock:
    return Mock(name="intermediate")
