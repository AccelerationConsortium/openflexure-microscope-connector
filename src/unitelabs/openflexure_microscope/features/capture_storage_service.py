"""SiLA feature for the images stored on the OpenFlexure Microscope."""

import typing

from unitelabs.cdk import sila
from unitelabs.cdk.sila import constraints

from ..io.api import Capability, MicroscopeApi
from ..io.errors import CaptureFailedError, CaptureNotFoundError, MicroscopeUnreachableError
from ..io.types import CaptureInfo
from ._common import CATEGORY, ORIGINATOR

Jpeg = typing.Annotated[bytes, constraints.ContentType("image", "jpeg")]
JsonDocument = typing.Annotated[bytes, constraints.ContentType("application", "json")]
_CAPTURE_ERRORS = (CaptureNotFoundError, CaptureFailedError, MicroscopeUnreachableError)


class CaptureStorageService(sila.Feature):
    """
    Browse, download, tag and delete the images stored on the OpenFlexure Microscope.

    Captures are created with `ImageAcquisitionController.CaptureToStorage` (or from the microscope's own web
    interface) and addressed by the identifier reported in `Captures`.
    """

    def __init__(self, api: MicroscopeApi) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api

    @sila.UnobservableProperty(errors=[CaptureFailedError, MicroscopeUnreachableError])
    async def get_captures(self) -> list[CaptureInfo]:
        """The captures currently stored on the microscope."""
        return await self._api.list_captures()

    @sila.UnobservableCommand(errors=_CAPTURE_ERRORS)
    async def download_capture(self, capture_id: str) -> Jpeg:
        """
        Download a stored capture.

        Args:
          CaptureId: Identifier of the capture, as listed in `Captures`.

        Returns:
          Image: The stored JPEG image.
        """
        return await self._api.download_capture(capture_id)

    @sila.UnobservableCommand(errors=_CAPTURE_ERRORS)
    async def download_thumbnail(self, capture_id: str) -> Jpeg:
        """
        Download a small thumbnail of a stored capture.

        Args:
          CaptureId: Identifier of the capture, as listed in `Captures`.

        Returns:
          Image: The thumbnail as a JPEG image.
        """
        return await self._api.download_capture(capture_id, thumbnail=True)

    @sila.UnobservableCommand(errors=_CAPTURE_ERRORS)
    async def read_capture_metadata(self, capture_id: str) -> JsonDocument:
        """
        Read the metadata the microscope recorded with a capture (instrument settings, state and image details).

        Args:
          CaptureId: Identifier of the capture, as listed in `Captures`.

        Returns:
          Metadata: The capture metadata as a UTF-8 encoded JSON document.
        """
        return await self._api.read_capture_metadata(capture_id)

    @sila.UnobservableCommand(errors=_CAPTURE_ERRORS, enabled=lambda self: self._api.supports(Capability.CAPTURE_TAGS))
    async def set_capture_tags(self, capture_id: str, tags: list[str]) -> list[str]:
        """
        Replace the tags of a stored capture.

        Args:
          CaptureId: Identifier of the capture, as listed in `Captures`.
          Tags: The new set of tags; an empty list removes all tags.

        Returns:
          Tags: The tags now attached to the capture.
        """
        return await self._api.set_capture_tags(capture_id, tags)

    @sila.UnobservableCommand(errors=_CAPTURE_ERRORS)
    async def delete_capture(self, capture_id: str) -> None:
        """
        Delete a stored capture from the microscope.

        Args:
          CaptureId: Identifier of the capture, as listed in `Captures`.
        """
        await self._api.delete_capture(capture_id)
