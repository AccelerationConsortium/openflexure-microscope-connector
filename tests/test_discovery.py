import contextlib

import pytest
from unitelabs.cdk import SiLAServerConfig
from unitelabs.cdk.config.connector_base_config import DiscoveryConfig

from unitelabs.openflexure_microscope import create_app, discovery
from unitelabs.openflexure_microscope.config import OpenflexureMicroscopeConfig


def test_only_addresses_other_computers_can_reach_are_used_in_order_without_duplicates():
    assert discovery.usable_addresses(
        ["192.168.1.179", None, "169.254.83.107", "127.0.0.1", "127.0.1.1", "172.19.144.1", "192.168.1.179", "0.0.0.0"]
    ) == ["192.168.1.179", "172.19.144.1"]
    assert discovery.usable_addresses(["224.0.0.251", "not-an-address", "::1"]) == []


def test_the_default_route_address_comes_first(monkeypatch):
    monkeypatch.setattr(discovery, "_default_route_address", lambda: "192.168.1.179")
    monkeypatch.setattr(
        discovery, "_host_addresses", lambda: ["172.19.144.1", "192.168.1.179", "169.254.83.107", "127.0.1.1"]
    )
    assert discovery.reachable_addresses() == ["192.168.1.179", "172.19.144.1"]


def test_reachable_addresses_on_this_computer_are_plain_ipv4_strings():
    for address in discovery.reachable_addresses():  # whatever this machine has; may be empty when offline
        assert address.count(".") == 3
        assert not address.startswith(("127.", "169.254."))


def test_unset_interfaces_are_filled_in_only_while_the_connector_is_built(monkeypatch):
    monkeypatch.setattr(discovery, "reachable_addresses", lambda: ["192.168.1.179", "172.19.144.1"])
    config = OpenflexureMicroscopeConfig()
    assert config.discovery.network_interfaces is None
    with discovery.discovery_on_reachable_interfaces(config):
        assert config.discovery.network_interfaces == ["192.168.1.179", "172.19.144.1"]
    assert config.discovery.network_interfaces is None  # a config saved back to disk keeps `null`


def test_the_setting_is_restored_even_if_building_the_connector_fails(monkeypatch):
    monkeypatch.setattr(discovery, "reachable_addresses", lambda: ["10.0.0.5"])
    config = OpenflexureMicroscopeConfig()
    with pytest.raises(RuntimeError), discovery.discovery_on_reachable_interfaces(config):
        raise RuntimeError
    assert config.discovery.network_interfaces is None


@pytest.mark.parametrize("configured", [["10.9.8.7"], []])
def test_an_explicit_list_is_kept_as_configured(monkeypatch, configured):
    monkeypatch.setattr(discovery, "reachable_addresses", lambda: ["192.168.1.179"])
    config = OpenflexureMicroscopeConfig(discovery=DiscoveryConfig(network_interfaces=list(configured)))
    with discovery.discovery_on_reachable_interfaces(config):
        assert config.discovery.network_interfaces == configured  # [] keeps the old "operating system picks"


def test_nothing_changes_when_discovery_is_off_or_no_address_is_found(monkeypatch):
    monkeypatch.setattr(discovery, "reachable_addresses", list)
    config = OpenflexureMicroscopeConfig()
    with discovery.discovery_on_reachable_interfaces(config):
        assert config.discovery.network_interfaces is None
    off = OpenflexureMicroscopeConfig(discovery=None)
    with discovery.discovery_on_reachable_interfaces(off):
        assert off.discovery is None


async def test_create_app_announces_on_the_reachable_addresses_and_leaves_the_config_unset(monkeypatch):
    monkeypatch.setattr(discovery, "reachable_addresses", lambda: ["10.1.2.3"])
    config = OpenflexureMicroscopeConfig(sila_server=SiLAServerConfig(hostname="127.0.0.1", port=0, tls=False))
    config.cloud_servers.enabled = False
    gen = create_app(config)
    connector = await gen.__anext__()
    try:
        # The CDK's discovery service keeps the list it was built with (read once, in its constructor).
        assert connector._discovery.socket.interface_addresses == ["10.1.2.3"]
        assert config.discovery.network_interfaces is None
    finally:
        with contextlib.suppress(StopAsyncIteration):
            await gen.aclose()


def test_without_a_network_route_the_operating_system_keeps_choosing(monkeypatch):
    monkeypatch.setattr(discovery, "_default_route_address", lambda: None)  # e.g. Wi-Fi not connected yet
    monkeypatch.setattr(discovery, "_host_addresses", lambda: ["172.19.144.1"])  # only a virtual adapter
    assert discovery.reachable_addresses() == []
    config = OpenflexureMicroscopeConfig()
    with discovery.discovery_on_reachable_interfaces(config):
        assert config.discovery.network_interfaces is None


def test_the_route_probe_reports_no_route(monkeypatch):
    class NoRoute:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def connect(self, address):
            raise OSError(10051, "A socket operation was attempted to an unreachable network")

    monkeypatch.setattr(discovery.socket, "socket", NoRoute)
    assert discovery._default_route_address() is None


@pytest.mark.parametrize("error", [OSError("host not found"), UnicodeError("label too long")])
def test_host_addresses_are_empty_when_the_host_name_cannot_be_resolved(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(discovery.socket, "getaddrinfo", fail)
    assert discovery._host_addresses() == []


def test_the_sila_library_still_reads_the_interface_list_only_when_discovery_is_built():
    import inspect

    from sila.server.discovery import Discovery

    source = inspect.getsource(Discovery)
    constructor = inspect.getsource(Discovery.__init__)
    assert source.count("network_interfaces") == constructor.count("network_interfaces") == 1, (
        "unitelabs-sila now reads discovery.network_interfaces outside Discovery.__init__, so "
        "discovery_on_reachable_interfaces restores it too early"
    )
