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
