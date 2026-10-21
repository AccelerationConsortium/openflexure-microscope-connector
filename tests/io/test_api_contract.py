import inspect

from unitelabs.openflexure_microscope.io.api import MicroscopeApi
from unitelabs.openflexure_microscope.io.v2.client import V2Client


def test_v2_client_implements_every_abstract_member():
    assert V2Client.__abstractmethods__ == frozenset()
    for name in MicroscopeApi.__abstractmethods__:
        base = inspect.getattr_static(MicroscopeApi, name)
        impl = inspect.getattr_static(V2Client, name)
        assert impl is not base, f"{name} not overridden"
        if isinstance(base, property):
            assert isinstance(impl, property), name
            continue
        assert inspect.iscoroutinefunction(impl), name
        base_params = list(inspect.signature(base).parameters)
        impl_params = list(inspect.signature(impl).parameters)
        assert impl_params == base_params, f"{name}: {impl_params} != {base_params}"
