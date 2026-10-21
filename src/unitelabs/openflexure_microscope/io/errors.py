"""
Defined execution errors raised by the io layer.

Each class docstring becomes the SiLA Defined Execution Error description, and the CDK matches raised exceptions
by class name, so the io layer always raises these exact classes (never subclasses) with an actionable message.
"""


class MicroscopeUnreachableError(Exception):
    """
    The OpenFlexure Microscope server did not answer.

    Check that the microscope is powered on, connected to the network and reachable at the configured host and
    port (default `microscope.local:5000`), then retry.
    """


class StageUnavailableError(Exception):
    """
    The microscope has no usable stage.

    The server reports the stage as missing (`MissingStage`); the Sangaboard may be disconnected or not detected.
    Check the stage cabling and restart the OpenFlexure server.
    """


class StageBusyError(Exception):
    """
    The stage is currently used by another operation, for example an autofocus or a scan.

    Wait for the running operation to finish or cancel it, then retry the move.
    """


class MoveFailedError(Exception):
    """
    The stage move did not complete.

    The server reported an error or cancelled the move. The message contains the server's log entry; check the
    stage for mechanical limits and retry.
    """


class CameraUnavailableError(Exception):
    """
    The microscope camera is not available.

    The server reports the camera as missing (`MissingCamera`) or the camera is not streaming. Check the camera
    ribbon cable and restart the OpenFlexure server.
    """


class CaptureFailedError(Exception):
    """
    The image capture did not complete.

    The server reported an error or cancelled the capture. The message contains the server's log entry; check
    free disk space on the microscope and that the camera is streaming, then retry.
    """


class CaptureNotFoundError(Exception):
    """
    No stored capture exists with the given identifier.

    Read the `Captures` property to list the identifiers currently stored on the microscope.
    """


class CameraSettingUnsupportedError(Exception):
    """
    The camera on this microscope does not expose the requested setting.

    Picamera-specific settings (exposure mode, gains, white balance) are only available with a Raspberry Pi
    camera; a missing or non-Pi camera does not provide them.
    """


class AutofocusUnavailableError(Exception):
    """
    The autofocus extension is not installed on this microscope server.

    The `org.openflexure.autofocus` extension must be enabled on the OpenFlexure server to use autofocus commands.
    """


class AutofocusFailedError(Exception):
    """
    The autofocus routine did not complete.

    The server reported an error or cancelled the routine. The message contains the server's log entry; make sure
    the stage can move freely over the requested z range and retry.
    """


class OperationInProgressError(Exception):
    """
    A long-running operation (stage move, capture or autofocus) is still running on the microscope.

    Wait for it to finish or stop it with `StageController.Stop` or `AutofocusController.Stop`, then retry.
    """
