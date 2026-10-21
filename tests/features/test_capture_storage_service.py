import json

import pytest

from unitelabs.openflexure_microscope.features.capture_storage_service import CaptureStorageService
from unitelabs.openflexure_microscope.io import errors, types
from unitelabs.openflexure_microscope.io.api import Capability
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_JPEG


@pytest.fixture
def feature(api) -> CaptureStorageService:
    return CaptureStorageService(api)


@pytest.fixture
async def stored(api) -> types.CaptureInfo:
    return await api.capture_to_storage("cells", ["a"], types.CaptureResolution.FULL)


def test_feature_metadata(feature):
    assert feature.identifier == "CaptureStorageService"
    assert feature.attach() is True
    assert set(feature.commands) == {
        "DownloadCapture",
        "DownloadThumbnail",
        "ReadCaptureMetadata",
        "SetCaptureTags",
        "DeleteCapture",
    }
    assert set(feature.properties) == {"Captures"}


def test_set_capture_tags_is_gated_on_the_capability(api):
    api.capabilities = frozenset(api.capabilities - {Capability.CAPTURE_TAGS})
    feature = CaptureStorageService(api)
    feature.attach()
    assert "SetCaptureTags" not in feature.commands
    assert "DeleteCapture" in feature.commands


async def test_list_download_and_metadata(feature, stored):
    captures = await feature.get_captures()
    assert captures == [stored]
    assert await feature.download_capture(stored.id) == SIMULATED_JPEG
    assert await feature.download_thumbnail(stored.id) == SIMULATED_JPEG
    metadata = json.loads((await feature.read_capture_metadata(stored.id)).decode("utf-8"))
    assert metadata["image"]["name"] == "cells.jpeg"
    assert "instrument" in metadata


async def test_tags_and_delete(feature, stored):
    assert await feature.set_capture_tags(stored.id, ["x", "y"]) == ["x", "y"]
    assert (await feature.get_captures())[0].tags == ["x", "y"]
    assert await feature.delete_capture(stored.id) is None
    assert await feature.get_captures() == []


async def test_unknown_capture_raises_not_found(feature):
    with pytest.raises(errors.CaptureNotFoundError):
        await feature.download_capture("missing")
    with pytest.raises(errors.CaptureNotFoundError):
        await feature.download_thumbnail("missing")
    with pytest.raises(errors.CaptureNotFoundError):
        await feature.read_capture_metadata("missing")
    with pytest.raises(errors.CaptureNotFoundError):
        await feature.set_capture_tags("missing", ["a"])
    with pytest.raises(errors.CaptureNotFoundError):
        await feature.delete_capture("missing")
