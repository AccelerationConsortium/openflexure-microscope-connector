import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--microscope-host", default=None, help="Hostname or IP of a real OpenFlexure Microscope server.")
    parser.addoption("--microscope-port", default=5000, type=int, help="Port of the OpenFlexure Microscope server.")
    parser.addoption(
        "--allow-autofocus",
        action="store_true",
        default=False,
        help="Also run hardware tests that start an autofocus sweep (z moves by up to 2000 steps).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    no_host = pytest.mark.skip(reason="needs --microscope-host <host> pointing at a real OpenFlexure Microscope")
    no_autofocus = pytest.mark.skip(reason="sweeps z by up to 2000 steps; enable with --allow-autofocus")
    for item in items:
        if "hardware" in item.keywords and not config.getoption("--microscope-host"):
            item.add_marker(no_host)
        elif "autofocus_sweep" in item.keywords and not config.getoption("--allow-autofocus"):
            item.add_marker(no_autofocus)
