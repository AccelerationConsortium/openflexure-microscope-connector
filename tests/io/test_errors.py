import inspect

import pytest

from unitelabs.openflexure_microscope.io import errors

ALL_ERRORS = [
    errors.MicroscopeUnreachableError,
    errors.StageUnavailableError,
    errors.StageBusyError,
    errors.MoveFailedError,
    errors.CameraUnavailableError,
    errors.CaptureFailedError,
    errors.CaptureNotFoundError,
    errors.CameraSettingUnsupportedError,
    errors.AutofocusUnavailableError,
    errors.AutofocusFailedError,
    errors.OperationInProgressError,
    errors.TravelLimitExceededError,
    errors.ExportFailedError,
    errors.ZeroPositionBlockedError,
]


@pytest.mark.parametrize("error", ALL_ERRORS)
def test_defined_errors_are_plain_exceptions_with_actionable_docstrings(error):
    # The CDK derives the SiLA Defined Execution Error description from the class docstring,
    # and matches raised exceptions by class name, so each must derive from Exception directly.
    assert issubclass(error, Exception)
    assert error.__mro__[1] is Exception
    doc = inspect.getdoc(error)
    assert doc and len(doc) > 20
