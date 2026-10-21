import pytest
from unitelabs.cdk.config import ConfigurationError

from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig


def test_defaults_start_in_simulation_against_v2_on_microscope_local():
    config = OpenflexureMicroscopeConfig()
    assert config.simulation_mode is True
    assert config.api_version == "v2"
    assert config.host == "microscope.local"
    assert config.port == 5000
    assert config.request_timeout_s == 10.0
    assert config.action_poll_interval_s == 0.25
    assert config.position_poll_interval_s == 0.5


def test_sila_server_metadata_describes_a_microscope():
    config = OpenflexureMicroscopeConfig()
    assert config.sila_server.name == "OpenFlexure Microscope"
    assert config.sila_server.type == "Microscope"
    assert config.sila_server.vendor_url == "https://openflexure.org/"
    assert "OpenFlexure" in config.sila_server.description


def test_api_version_must_be_implemented():
    with pytest.raises(ConfigurationError):
        OpenflexureMicroscopeConfig.validate({"api_version": "v3"})


def test_stage_limits_default_to_unlimited_and_export_folder_to_downloads():
    import pathlib

    config = OpenflexureMicroscopeConfig()
    for axis in "xyz":
        assert getattr(config, f"stage_{axis}_min") is None
        assert getattr(config, f"stage_{axis}_max") is None
    assert config.export_folder == "~/Downloads/OpenFlexure"  # written as-is by `config create`, portable
    assert pathlib.Path(config.export_folder).expanduser() == pathlib.Path.home() / "Downloads" / "OpenFlexure"


def test_stage_limits_must_come_in_ordered_pairs():
    with pytest.raises(ConfigurationError, match="stage_x"):
        OpenflexureMicroscopeConfig.validate({"stage_x_min": -5})
    with pytest.raises(ConfigurationError, match="stage_z"):
        OpenflexureMicroscopeConfig.validate({"stage_z_min": 10, "stage_z_max": 5})
    config = OpenflexureMicroscopeConfig.validate({"stage_y_min": -1, "stage_y_max": 1})
    assert (config.stage_y_min, config.stage_y_max) == (-1, 1)


@pytest.mark.parametrize("folder", ["", "   "])
def test_an_empty_export_folder_is_rejected(folder):
    with pytest.raises(ConfigurationError, match="export_folder"):
        OpenflexureMicroscopeConfig.validate({"export_folder": folder})
