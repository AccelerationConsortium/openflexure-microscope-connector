"""
Choose the network interfaces the connector announces itself on for SiLA discovery (mDNS).

When `discovery.network_interfaces` is left unset, the SiLA library lets the operating system pick one interface for
its announcements. Windows often picks a virtual adapter (Tailscale or another VPN, WSL, Docker Desktop), so nobody
else on the lab network finds the connector in Ground Control. The connector therefore announces on the default
route's address and the other addresses the operating system reports for the host name. The list is fixed at start:
if an address disappears while the connector runs (a new DHCP lease, another network), discovery goes quiet on it
until the connector restarts.
"""

import collections.abc
import contextlib
import ipaddress
import socket

from unitelabs.cdk import ConnectorBaseConfig, get_logger

log = get_logger(__name__)

# TEST-NET-1 (RFC 5737). Connecting a UDP socket only asks the operating system which route and local address it
# would use; no packet is sent.
_ROUTE_PROBE = ("192.0.2.1", 9)


def reachable_addresses() -> list[str]:
    """
    Return the IPv4 addresses to announce the connector on.

    These are the address of the default route, then the IPv4 addresses the operating system reports for the host
    name, without loopback and link-local ones. Empty when there is no default route (the computer is not connected
    yet), so that the operating system keeps choosing instead of discovery being pinned to virtual adapters.
    """
    route = _default_route_address()
    if route is None:
        return []
    return usable_addresses([route, *_host_addresses()])


def usable_addresses(candidates: collections.abc.Iterable[str | None]) -> list[str]:
    """
    Keep the IPv4 addresses another computer can reach, in order and without duplicates.

    Loopback, link-local (169.254.x.x: an interface without a network), unspecified and multicast addresses are
    left out, as is anything that is not an IPv4 address.
    """
    addresses: list[str] = []
    for candidate in candidates:
        if candidate is None or candidate in addresses:
            continue
        try:
            address = ipaddress.IPv4Address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            continue
        addresses.append(candidate)
    return addresses


@contextlib.contextmanager
def discovery_on_reachable_interfaces(config: ConnectorBaseConfig) -> collections.abc.Iterator[None]:
    """
    Fill in `discovery.network_interfaces` while the connector is built, if the configuration leaves it unset.

    The SiLA library reads the list once, when the connector creates its discovery service, so the setting is
    restored afterwards: a configuration the connector saves back to disk (e.g. after a server rename) keeps `null`,
    and the addresses are looked up again at the next start. An explicit list is kept as configured, including an
    empty one (the operating system picks the interface), and nothing changes when discovery is off.
    """
    settings = config.discovery
    if settings is None or settings.network_interfaces is not None:
        yield
        return
    addresses = reachable_addresses()
    if not addresses:
        log.warning(
            "Found no network connection for discovery at start; the operating system picks the interface. Restart "
            "the connector once the network is up, or set discovery.network_interfaces in config.json."
        )
        yield
        return
    log.info("Announcing the connector for discovery on %s.", ", ".join(addresses))
    settings.network_interfaces = addresses
    try:
        yield
    finally:
        settings.network_interfaces = None


def _default_route_address() -> str | None:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(_ROUTE_PROBE)
        return probe.getsockname()[0]
    return None


def _host_addresses() -> list[str]:
    with contextlib.suppress(OSError, UnicodeError):  # UnicodeError: a host name the IDNA codec rejects
        return [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    return []
