# OpenFlexure Microscope — UniteLabs Connector

A SiLA 2 connector for the [OpenFlexure Microscope](https://openflexure.org/), built with the
[UniteLabs CDK](https://docs.unitelabs.io/connector-development/) in the same style as the Acceleration Consortium's
[opentrons-ot2](https://github.com/AccelerationConsortium/opentrons-ot2) connector.

It wraps the HTTP API of the **OpenFlexure Microscope Server v2** (`http://<microscope>:5000/api/v2`, server 2.x on the
Raspbian-OpenFlexure image) as SiLA 2 features: stage movement, image acquisition, stored captures, camera settings,
autofocus and device information. The connector starts in **simulation mode** by default and can switch to the real
microscope at runtime.

## Status

Version 0.1.0, not yet released.

- **Verified on a real microscope** (server 2.11.0, Raspberry Pi camera v2, 2026-10-21): identity and status, camera
  settings, preview and full-resolution images, stored captures (create, list, download, tag, delete), sharpness
  measurement, simulation/real mode switching, and the defined errors for unavailable hardware. It was driven over SiLA
  and from UniteLabs Ground Control.
- **Not yet verified on hardware:** stage motion, `StageController.Stop`, autofocus (`RunFastAutofocus`,
  `RunAutofocus`, `AutofocusController.Stop`) and the server's busy/error responses. The test microscope's motors were
  detached, so the connector correctly refused these commands.
- **Not yet tried:** the Docker image and registration with the UniteLabs cloud platform.

## Features

| SiLA feature | Commands | Properties | OpenFlexure v2 endpoints |
|---|---|---|---|
| `StageController` | `MoveTo`*, `MoveBy`*, `SetZeroPosition`, `Stop` | `Position`, `LivePosition`†, `StageType`, `Backlash` | `actions/stage/move`, `actions/stage/zero`, `instrument/state/stage/position` |
| `ImageAcquisitionController` | `GrabPreviewFrame`, `AcquireImage`*, `CaptureToStorage`* | `MJPEGStreamURL` | `streams/snapshot`, `actions/camera/ram-capture`, `actions/camera/capture` |
| `CaptureStorageService` | `DownloadCapture`, `DownloadThumbnail`, `ReadCaptureMetadata`, `SetCaptureTags`, `DeleteCapture` | `Captures` | `captures`, `captures/{id}`, `captures/{id}/download/{name}`, `captures/{id}/tags` |
| `CameraController` | `SetExposureTime`, `SetAnalogGain`, `SetWhiteBalanceGains`, `SetDigitalGain`‡, `SetExposureMode`‡, `SetWhiteBalanceMode`‡, `SetJPEGQuality`‡ | `ExposureTime`, `AnalogGain`, `WhiteBalanceGains`, `StreamResolution`, `ImageResolution`, `DigitalGain`‡, `ExposureMode`‡, `WhiteBalanceMode`‡, `JPEGQuality`‡ | `instrument/settings/camera/...` |
| `AutofocusController` | `RunFastAutofocus`*, `RunAutofocus`*, `MeasureSharpness`, `Stop` | — | `extensions/org.openflexure.autofocus/{fast_autofocus,autofocus,measure_sharpness}` |
| `MicroscopeProvider` | — | `MicroscopeName`, `MicroscopeId`, `ServerVersion`, `CameraType`, `InstalledExtensions`, `APIBaseURL`, `Reachable`† | `instrument/configuration`, `instrument/settings`, `extensions` |
| `SimulationController` (SiLA core) | `StartSimulationMode`, `StartRealMode` | `SimulationMode` | — |
| `LockController` (SiLA core) | `LockServer`, `UnlockServer` | `IsLocked` | — (see note §) |

\* observable command (progress + intermediate responses, execution mode `SINGLE`)   † observable property
‡ only offered when the microscope's camera supports it (Raspberry Pi camera)
§ the lock is not enforced: no command requires the lock identifier yet, so locking does not block other clients

Conventions on the SiLA interface:

- Stage positions and distances are **motor steps** (integers), exactly as the OpenFlexure server reports them.
- Images are SiLA `Binary` values with content type `image/jpeg`. Images larger than 2 MiB (full-resolution captures)
  travel through SiLA binary transfer automatically.
- Enumerations travel as their member names in lower case with spaces, e.g. `ExposureMode` accepts `off`, `auto`,
  `night preview`, …; `Resolution` accepts `preview` or `full`; `StageType` reports `sanga stage`, `sanga delta stage`
  or `missing stage`.
- The SiLA 2 base protocol has no cancel call, and this connector does not implement the optional `CancelController`
  feature, so a client cannot abort the command it started. Use `StageController.Stop` or `AutofocusController.Stop`
  instead: they cancel the running action on the microscope, and the cancelled command finishes with a
  `MoveFailedError` / `AutofocusFailedError` that says so.
- Every failure is a *Defined Execution Error* with an actionable message (`MicroscopeUnreachableError`,
  `StageUnavailableError`, `StageBusyError`, `MoveFailedError`, `CameraUnavailableError`, `CaptureFailedError`,
  `CaptureNotFoundError`, `CameraSettingUnsupportedError`, `AutofocusUnavailableError`, `AutofocusFailedError`).

## Getting started

### Prerequisites

- Python 3.10 or newer (the repository pins 3.13 in `.python-version`) and [uv](https://docs.astral.sh/uv/).
- For real mode: an OpenFlexure Microscope running server **v2.x** reachable on the network (default
  `microscope.local:5000`; the IP address works as well if `.local` names do not resolve on your machine).

### Install and run (simulation)

```sh
uv sync --all-extras
uv run config create --app unitelabs.openflexure_microscope:create_app   # writes ./config.json (git-ignored)
uv run connector start --app unitelabs.openflexure_microscope:create_app
```

The log shows `Server bound to '0.0.0.0:<port>'` and `Registering service 'OpenFlexure Microscope'`. Use the
[SiLA Browser](https://gitlab.com/unitelabs/sila2/sila-browser) to discover and try the server, or run the smoke client:

```sh
uv run python scripts/smoke_client.py --port <port>              # read identity, position, grab a preview frame
uv run python scripts/smoke_client.py --port <port> --move-z 20  # also move the stage up 20 steps and back
```

> **Windows note.** `--app` is required on Windows (the CDK cannot auto-detect the factory). If your machine blocks
> the `.venv\Scripts\*.exe` launchers (Windows Application Control), call the CDK commands through Python instead:
>
> ```sh
> uv run python -c "from unitelabs.cdk.cli import config; config()" create --app unitelabs.openflexure_microscope:create_app
> uv run python -c "from unitelabs.cdk.cli import connector; connector()" start --app unitelabs.openflexure_microscope:create_app
> uv run python -m pytest
> ```

### Connect to the real microscope

Edit `config.json` (a complete example is in [`config/openflexure_config.example.json`](config/openflexure_config.example.json)):

```json
{
  "simulation_mode": false,
  "host": "microscope.local",
  "port": 5000
}
```

Then start the connector again. In real mode it probes `GET /api/v2/` and `GET /api/v2/instrument/configuration` at
startup and **refuses to start** (no silent fallback to simulation) if the microscope does not answer or does not
serve the v2 API. You can also keep `simulation_mode: true` and switch at runtime with
`SimulationController.StartRealMode`; if the microscope is unreachable the command fails with `StartRealModeFailed`
and the connector stays simulated. Switching in either direction is refused while a move, capture or autofocus
is still running (stop it first).

### Configuration

| Key | Default | Meaning |
|---|---|---|
| `simulation_mode` | `true` | Serve the in-memory simulated microscope instead of talking to hardware. |
| `api_version` | `"v2"` | OpenFlexure server API generation. Only `v2` is implemented. |
| `host` / `port` | `"microscope.local"` / `5000` | Address of the OpenFlexure Microscope server. |
| `request_timeout_s` | `10.0` | Timeout of a single HTTP request. |
| `action_poll_interval_s` | `0.25` | Poll interval for long-running server actions (move, capture, autofocus). |
| `position_poll_interval_s` | `0.5` | Poll interval behind the `LivePosition` subscription. |
| `sila_server`, `cloud_servers`, `discovery`, `logging` | CDK defaults | Standard UniteLabs settings (local port, UniteLabs cloud endpoints, mDNS, logging). |

Set `sila_server.port` to a fixed value (e.g. `50052`) for a stable local endpoint. To register the connector with the
UniteLabs platform, add your tenant's cloud endpoint under `cloud_servers.endpoints` (not yet tried with this connector).
Each installation generates its own `sila_server.uuid`; keep it stable once the connector is registered.

## Testing

```sh
uv run python -m pytest                       # unit tests, simulated backend, gRPC round trips (hardware tests skipped)
uv run python -m pytest tests/integration --microscope-host microscope.local   # against a real microscope
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
```

The hardware tests read identity, position and settings, grab a preview frame and in-memory captures (preview and full
resolution), store one test capture and delete it again, and measure sharpness. With a stage attached they also move z
by 20 steps and back. They are the final verification step for a new microscope; everything else runs without hardware.

Add `--allow-autofocus` to also run the autofocus `Stop` test. It starts a fast autofocus that sweeps z by up to 2000
steps, stops it after a second and moves z back to where it started. Make sure the objective and sample have room for
that before using the flag.

## Docker

Not yet tested: the image has not been built and run against a microscope.

```sh
uv run config create --app unitelabs.openflexure_microscope:create_app   # config.json is mounted into the container
docker compose up --build
```

`docker-compose.yml` uses host networking so mDNS discovery and `microscope.local` work; that requires a Linux Docker
host. On Docker Desktop (Windows/macOS) publish the SiLA port instead and set `host` to the microscope's IP address.

## Limitations and roadmap

- **Server v2 only.** The v3 beta server (labthings-fastapi, `/api/v3`) has a different API. The io layer is built
  around a version-neutral `MicroscopeApi` contract (`io/api.py`), so a v3 implementation can be added as `io/v3/`
  without touching the SiLA features; `api_version` selects the implementation.
- **No illumination control.** The v2 HTTP API has no LED endpoint; it only exists on v3 (planned follow-up via
  `Capability.ILLUMINATION`).
- **Autofocus** needs the `org.openflexure.autofocus` extension on the microscope (installed by default).
- `config show` crashes in UniteLabs CDK 0.16.0 on the base `sila_server.version` field (a CDK bug); use
  `config create` / `config schema` or the example config instead.

## Development

```sh
uv sync --all-extras
uv run pre-commit install
uv run ruff format src tests && uv run ruff check src tests --fix
uv run python -m pytest
```

Layout (see [AGENTS.md](AGENTS.md) for the design rules):

```
src/unitelabs/openflexure_microscope/
  __init__.py            create_app(): builds the API client, registers the features
  config.py              OpenflexureMicroscopeConfig
  io/api.py              MicroscopeApi contract + Capability + build_api()
  io/types.py, errors.py, units.py
  io/v2/backend.py       HttpBackend (httpx) and the Backend protocol
  io/v2/simulation.py    SimulatedBackend: in-memory OpenFlexure v2 server
  io/v2/client.py        V2Client: MicroscopeApi over the v2 HTTP API (LabThings actions, polling, cancellation)
  features/              one SiLA feature per module, thin wrappers over MicroscopeApi
tests/                   unit tests, SiLA definition and gRPC round-trip tests, opt-in hardware tests
```

The project was generated from the UniteLabs [connector-factory](https://gitlab.com/unitelabs/cdk/connector-factory)
template (`.cruft.json`) and depends only on PyPI packages (`unitelabs-cdk`, `httpx`).

## Contact

Issues: https://github.com/AccelerationConsortium/openflexure-microscope/issues
