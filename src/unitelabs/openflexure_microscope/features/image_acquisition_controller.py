"""SiLA feature for acquiring images from the OpenFlexure Microscope camera."""

import typing

from unitelabs.cdk import sila
from unitelabs.cdk.sila import constraints

from ..io.api import MicroscopeApi
from ..io.errors import CameraUnavailableError, CaptureFailedError, MicroscopeUnreachableError
from ..io.types import Annotation, CaptureInfo, CaptureResolution
from ._common import CATEGORY, ORIGINATOR
from ._progress import OperationProgress, run_observable

Jpeg = typing.Annotated[bytes, constraints.ContentType("image", "jpeg")]
_CAPTURE_ERRORS = (CameraUnavailableError, CaptureFailedError, MicroscopeUnreachableError)


class ImageAcquisitionController(sila.Feature):
    """
    Acquire images from the OpenFlexure Microscope camera.

    `GrabPreviewFrame` returns the next frame of the live preview stream and is fast. `AcquireImage` takes a
    dedicated capture into memory; at full resolution the preview stream pauses for a few seconds. `CaptureToStorage`
    takes a capture and keeps it on the microscope, where `CaptureStorageService` can list, download and delete it.
    All images are JPEG.
    """

    def __init__(self, api: MicroscopeApi) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api

    @sila.UnobservableProperty(name="MJPEG Stream URL")
    async def get_mjpeg_stream_url(self) -> str:
        """URL of the camera's live MJPEG preview stream."""
        return self._api.mjpeg_stream_url

    @sila.UnobservableCommand(errors=[CameraUnavailableError, MicroscopeUnreachableError])
    async def grab_preview_frame(self) -> Jpeg:
        """
        Return the next frame of the live preview stream.

        Returns:
          Image: The preview frame as a JPEG image.
        """
        return await self._api.grab_preview_frame()

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_CAPTURE_ERRORS)
    async def acquire_image(
        self,
        resolution: CaptureResolution,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> Jpeg:
        """
        Capture an image into memory and return it.

        Args:
          Resolution: Whether to capture at the preview stream resolution (fast) or at the full still-image
            resolution (slow, pauses the preview stream).

        Yields:
          Progress: Lifecycle updates of the capture.

        Returns:
          Image: The captured JPEG image.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Capturing a {resolution.value} resolution image.",
            complete="Image captured.",
            cancelled="Image capture cancelled.",
            action=lambda on_progress: self._api.acquire_image(resolution, on_progress=on_progress),
        )

    @sila.ObservableCommand(mode=sila.ExecutionMode.SINGLE, errors=_CAPTURE_ERRORS)
    async def capture_to_storage(
        self,
        filename: str,
        tags: list[str],
        annotations: list[Annotation],
        resolution: CaptureResolution,
        *,
        status: sila.Status,
        intermediate: sila.Intermediate[OperationProgress],
    ) -> CaptureInfo:
        """
        Capture an image and store it on the microscope.

        Args:
          Filename: File name for the capture without extension; leave empty to let the microscope use a timestamp.
          Tags: Tags to attach to the stored capture; may be empty.
          Annotations: Key/value notes to store with the capture, e.g. a sample identifier; keys must be unique.
            May be empty.
          Resolution: Whether to capture at the preview stream resolution or at the full still-image resolution.

        Yields:
          Progress: Lifecycle updates of the capture.

        Returns:
          Capture: Description of the stored capture, including the identifier used by `CaptureStorageService`.
        """
        return await run_observable(
            status,
            intermediate,
            start=f"Capturing a {resolution.value} resolution image to the microscope storage.",
            complete="Image stored on the microscope.",
            cancelled="Image capture cancelled.",
            action=lambda on_progress: self._api.capture_to_storage(
                filename,
                tags,
                resolution,
                on_progress=on_progress,
                annotations={note.key: note.value for note in annotations},
            ),
        )

    @capture_to_storage.validator
    def _check_annotations(self, annotations: list[Annotation]) -> None:
        keys = [note.key for note in annotations]
        if any(not key.strip() for key in keys):
            msg = "Annotation keys must not be empty."
            raise sila.errors.ValidationError(msg, "annotations")
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            msg = f"Annotation keys must be unique; duplicate keys: {', '.join(duplicates)}."
            raise sila.errors.ValidationError(msg, "annotations")
