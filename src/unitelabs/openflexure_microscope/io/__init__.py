"""
Device communication layer.

`api` holds the version-neutral contract the SiLA features depend on; `v2` implements it for the OpenFlexure
Microscope Server v2 HTTP API. Nothing in this package knows about SiLA.
"""

from . import api, errors, types, units
from .api import Capability, MicroscopeApi, build_api

__all__ = ["Capability", "MicroscopeApi", "api", "build_api", "errors", "types", "units"]
