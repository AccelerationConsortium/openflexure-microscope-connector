# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial UniteLabs SiLA 2 connector for the OpenFlexure Microscope Server v2 (`/api/v2`):
  `StageController`, `ImageAcquisitionController`, `CaptureStorageService`, `CameraController`,
  `AutofocusController`, `MicroscopeProvider`, plus the core `SimulationController` and `LockController`.
- In-memory simulation of the v2 HTTP API (default mode) and runtime switching to the real microscope.
- Version-neutral `MicroscopeApi` contract so a v3 (labthings-fastapi) implementation can be added later.
- Software stage travel limits (`stage_{x,y,z}_{min,max}` in the config, off by default): moves and autofocus
  sweeps outside the range, including the stage's backlash overshoot, fail with `TravelLimitExceededError`
  before any move reaches the microscope; only the axes a move changes are checked, and one move or autofocus runs at a
  time while limits are set;
  `StageController.TravelLimits` reports them, and `SetZeroPosition` is refused with `ZeroPositionBlockedError`
  while they are set.
- Capture annotations: `ImageAcquisitionController.CaptureToStorage` takes an `Annotations` list of key/value
  pairs, and `Captures` reports them.
- `CaptureStorageService.ExportCapture` saves a stored capture into `export_folder` (default
  `~/Downloads/OpenFlexure`) on the computer running the connector without overwriting existing files;
  `CaptureStorageService.ExportFolder` reports the folder.

### Changed

- `StageController.Stop` and `AutofocusController.Stop` also stop a move or autofocus that has not reached the
  microscope yet, count each stopped operation once (and a running action only once the microscope accepted its
  cancellation), and return once the stopped commands have ended.
- A running action whose status cannot be read three times in a row is cancelled on the microscope instead of
  being left running untracked; a single failed status check is retried.
- While travel limits are set on a delta stage, moves that would correct backlash and the slow `RunAutofocus`
  are refused with `TravelLimitExceededError`, because their overshoot cannot be checked.
- `ExportCapture` file names always end in `.jpeg` (or keep `.jpg`), never start with a dot, skip a folder of the
  same name, and a relative `export_folder` is reported as a full path. `docker-compose.yml` mounts `./exports`
  for exported images.
- With `discovery.network_interfaces` left `null`, the connector announces itself for discovery on the default
  route's address and the host name's other IPv4 addresses instead of letting the operating system pick one
  interface, so colleagues' Ground Control finds it on Windows PCs with VPN, WSL or Docker Desktop adapters.
  Without a network connection at start it keeps the old behaviour and warns; `[]` keeps the old behaviour.
- `CaptureToStorage` has a new required `Annotations` parameter. Clients that called it before must now pass
  `Annotations` (an empty list keeps the old behaviour).
