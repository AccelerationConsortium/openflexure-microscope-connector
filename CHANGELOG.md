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
