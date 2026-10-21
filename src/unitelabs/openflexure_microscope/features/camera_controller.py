"""SiLA feature for the camera settings of the OpenFlexure Microscope."""

import typing

from unitelabs.cdk import sila
from unitelabs.cdk.sila import constraints

from ..io.api import Capability, MicroscopeApi
from ..io.errors import CameraSettingUnsupportedError, CameraUnavailableError, MicroscopeUnreachableError
from ..io.types import ExposureMode, Resolution, WhiteBalanceGains, WhiteBalanceMode
from ..io.units import MICROSECOND, PERCENT
from ._common import CATEGORY, ORIGINATOR

ExposureTime = typing.Annotated[int, MICROSECOND, constraints.MinimalInclusive(0)]
Gain = typing.Annotated[float, constraints.MinimalInclusive(0.0)]
JpegQuality = typing.Annotated[int, PERCENT, constraints.MinimalInclusive(1), constraints.MaximalInclusive(100)]
_SETTING_ERRORS = (CameraSettingUnsupportedError, CameraUnavailableError, MicroscopeUnreachableError)


class CameraController(sila.Feature):
    """
    Read and change the camera settings of the OpenFlexure Microscope.

    Exposure time, analog gain and white balance gains are available on every camera. Digital gain, the automatic
    exposure and white balance modes and the JPEG quality are only offered when the microscope's camera supports
    them (Raspberry Pi camera). Setting the white balance gains switches the automatic white balance off.
    """

    def __init__(self, api: MicroscopeApi) -> None:
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self._api = api

    # -- always available ----------------------------------------------------------------------------------------

    @sila.UnobservableProperty(errors=_SETTING_ERRORS)
    async def get_exposure_time(self) -> ExposureTime:
        """The exposure (shutter) time of the camera."""
        return await self._api.get_exposure_time_us()

    @sila.UnobservableCommand(errors=_SETTING_ERRORS)
    async def set_exposure_time(self, exposure_time: ExposureTime) -> None:
        """
        Set the exposure (shutter) time of the camera.

        Args:
          ExposureTime: The new exposure time; 0 lets the camera choose automatically.
        """
        await self._api.set_exposure_time_us(exposure_time)

    @sila.UnobservableProperty(errors=_SETTING_ERRORS)
    async def get_analog_gain(self) -> Gain:
        """The analog gain of the camera sensor."""
        return await self._api.get_analog_gain()

    @sila.UnobservableCommand(errors=_SETTING_ERRORS)
    async def set_analog_gain(self, gain: Gain) -> None:
        """
        Set the analog gain of the camera sensor.

        Args:
          Gain: The new analog gain.
        """
        await self._api.set_analog_gain(gain)

    @sila.UnobservableProperty(errors=_SETTING_ERRORS)
    async def get_white_balance_gains(self) -> WhiteBalanceGains:
        """The manual white balance gains of the camera."""
        return await self._api.get_white_balance_gains()

    @sila.UnobservableCommand(errors=_SETTING_ERRORS)
    async def set_white_balance_gains(self, red: Gain, blue: Gain) -> None:
        """
        Set the manual white balance gains; this switches the automatic white balance off.

        Args:
          Red: Gain applied to the red channel.
          Blue: Gain applied to the blue channel.
        """
        await self._api.set_white_balance_gains(red, blue)

    @sila.UnobservableProperty(errors=_SETTING_ERRORS)
    async def get_stream_resolution(self) -> Resolution:
        """The resolution of the preview stream."""
        return await self._api.get_stream_resolution()

    @sila.UnobservableProperty(errors=_SETTING_ERRORS)
    async def get_image_resolution(self) -> Resolution:
        """The resolution of full still-image captures."""
        return await self._api.get_image_resolution()

    # -- camera dependent ----------------------------------------------------------------------------------------

    @sila.UnobservableProperty(errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.DIGITAL_GAIN))
    async def get_digital_gain(self) -> Gain:
        """The digital gain of the camera."""
        return await self._api.get_digital_gain()

    @sila.UnobservableCommand(errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.DIGITAL_GAIN))
    async def set_digital_gain(self, gain: Gain) -> None:
        """
        Set the digital gain of the camera.

        Args:
          Gain: The new digital gain.
        """
        await self._api.set_digital_gain(gain)

    @sila.UnobservableProperty(
        errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.EXPOSURE_MODE)
    )
    async def get_exposure_mode(self) -> ExposureMode:
        """The automatic exposure mode of the camera."""
        return await self._api.get_exposure_mode()

    @sila.UnobservableCommand(errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.EXPOSURE_MODE))
    async def set_exposure_mode(self, mode: ExposureMode) -> None:
        """
        Set the automatic exposure mode of the camera.

        Args:
          Mode: The exposure mode; `off` keeps the current exposure time and gains fixed.
        """
        await self._api.set_exposure_mode(mode)

    @sila.UnobservableProperty(
        errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.WHITE_BALANCE_MODE)
    )
    async def get_white_balance_mode(self) -> WhiteBalanceMode:
        """The automatic white balance mode of the camera."""
        return await self._api.get_white_balance_mode()

    @sila.UnobservableCommand(
        errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.WHITE_BALANCE_MODE)
    )
    async def set_white_balance_mode(self, mode: WhiteBalanceMode) -> None:
        """
        Set the automatic white balance mode of the camera.

        Args:
          Mode: The white balance mode; `off` keeps the manual white balance gains.
        """
        await self._api.set_white_balance_mode(mode)

    @sila.UnobservableProperty(
        name="JPEG Quality", errors=_SETTING_ERRORS, enabled=lambda self: self._api.supports(Capability.JPEG_QUALITY)
    )
    async def get_jpeg_quality(self) -> JpegQuality:
        """The JPEG compression quality used for captured images."""
        return await self._api.get_jpeg_quality()

    @sila.UnobservableCommand(
        name="Set JPEG Quality",
        errors=_SETTING_ERRORS,
        enabled=lambda self: self._api.supports(Capability.JPEG_QUALITY),
    )
    async def set_jpeg_quality(self, quality: JpegQuality) -> None:
        """
        Set the JPEG compression quality used for captured images.

        Args:
          Quality: The compression quality; 100 is the least compressed.
        """
        await self._api.set_jpeg_quality(quality)
