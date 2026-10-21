"""Every device endpoint declares the transport error, so an unplugged microscope is a *defined* SiLA error."""

import pytest

from unitelabs.openflexure_microscope.features import (
    AutofocusController,
    CameraController,
    CaptureStorageService,
    ImageAcquisitionController,
    MicroscopeProvider,
    StageController,
)


LOCAL_ONLY = {"MJPEGStreamURL", "APIBaseURL", "Reachable"}
"""Endpoints that answer from local state or swallow transport errors by design."""


@pytest.mark.parametrize(
    "feature_cls",
    [
        StageController,
        ImageAcquisitionController,
        CaptureStorageService,
        CameraController,
        AutofocusController,
        MicroscopeProvider,
    ],
)
def test_every_endpoint_declares_microscope_unreachable(api, feature_cls):
    feature = feature_cls(api)
    feature.attach()
    handlers = {**feature.commands, **feature.properties}
    assert handlers
    for identifier, handler in handlers.items():
        if identifier in LOCAL_ONLY:
            continue
        assert "MicroscopeUnreachableError" in handler.errors, f"{feature_cls.__name__}.{identifier}"
