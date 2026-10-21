"""
Opt-in tests against a real OpenFlexure Microscope.

Run with `uv run python -m pytest tests/integration --microscope-host microscope.local` (add `--microscope-port` if the
server does not listen on 5000, and `--allow-autofocus` to include the autofocus sweep). Without `--microscope-host`
every test here is skipped. The command-line options are defined in `tests/conftest.py`.
"""

import pytest

from unitelabs.openflexure_microscope.io.v2.client import V2Client


@pytest.fixture
async def real_api(request: pytest.FixtureRequest) -> V2Client:
    host = request.config.getoption("--microscope-host")
    port = request.config.getoption("--microscope-port")
    api = V2Client(real_base_url=f"http://{host}:{port}/api/v2", simulated=False, action_poll_interval_s=0.25)
    await api.open()
    try:
        yield api
    finally:
        await api.aclose()
