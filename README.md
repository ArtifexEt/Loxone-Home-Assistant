# Loxone

[![CI](https://github.com/ArtifexEt/Loxone-Home-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtifexEt/Loxone-Home-Assistant/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant-community-store&logoColor=white)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-18BCF2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)

Custom HACS integration for local Loxone Miniserver, Miniserver Go, and Miniserver Compact control from Home Assistant.

## Quick Start in Home Assistant

1. Add this repository to HACS:

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ArtifexEt&repository=Loxone-Home-Assistant&category=integration)

2. Install `Loxone` in HACS and restart Home Assistant.
3. Start the integration setup flow:

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=loxone_home_assistant)

If the button does not open the flow directly, use `Settings -> Devices & Services -> Add Integration -> Loxone`.

## What it does

- asks for Loxone username and password during setup
- scans the local network for a Loxone server (Miniserver, Miniserver Go, Miniserver Compact)
- connects locally over secure WebSocket
- imports the Loxone structure from `LoxAPP3.json`
- keeps two-way state sync between Home Assistant and Loxone
- exposes common Loxone blocks as Home Assistant entities
- exposes Intercom/IntercomV2 (and DoorController/DoorStation variants) as dedicated doorbell entities:
  - camera stream + snapshot preview when available
  - binary sensors for ring, call, proximity, and intercom light state
  - Intercom Gen2 action buttons (`Answer Call`, `Mute Microphone`, `Unmute Microphone`)
  - intercom history sensor based on `lastBellEvents` (latest event timestamp + recent image URLs)
  - Home Assistant bus events (`loxone_home_assistant_intercom_event`) for automation triggers
- exposes `AudioZone`/`AudioZoneV2`/`CentralAudioZone` as `media_player` with source selection, seek/progress, shuffle/repeat, TTS and event/command passthrough via `play_media`
- exposes `PresenceDetector` as:
  - `binary_sensor` for presence/motion
  - `sensor` for illuminance (`lx`) and sound level (`dB`)
- exposes `Tracker` as event/history sensors for log-like states
- exposes Miniserver Web Services diagnostics as hub-level sensors:
  - `dev/sys/numtasks` (system tasks)
  - `dev/sys/cpu` (CPU load)
  - `dev/sys/heap` (memory usage)
  - `dev/sys/ints` (system interrupts)
- adds hub-level maintenance actions (for example server restart)
- adds a hub action button to force diagnostics refresh (`Refresh System Stats`)
- registers integration services once in integration setup (HA-compatible service lifecycle)

## Supported platforms

- `switch`
- `light`
- `cover`
- `climate`
- `sensor`
- `binary_sensor`
- `button`
- `camera`
- `number`
- `select`
- `text`
- `media_player`

## Supported Loxone block types

- `Switch`
- `TimedSwitch`
- `Pushbutton`
- `Dimmer`
- `ColorPicker`
- `ColorPickerV2`
- `LightsceneRGB`
- `LightController`
- `LightControllerV2`
- `Jalousie`
- `IRoomController`
- `IRoomControllerV2`
- `InfoOnlyAnalog`
- `InfoOnlyDigital`
- `PresenceDetector`
- `PowerSupply`
- `PowerSupplyV2`
- `Alarm`
- `AlarmClock`
- `CarCharger`
- `Daytimer`
- `Fronius`
- `Gate`
- `Heatmixer`
- `Hourcounter`
- `Intercom`
- `PoolController`
- `Radio`
- `Remote`
- `Sauna`
- `Tracker`
- `UpDownLeftRight` (digital and analog)
- `ValueSelector`
- `Webpage`
- `TextState`
- `Meter`
- `Slider`
- `TextInput`
- `AudioZone`
- `AudioZoneV2`
- `CentralAudioZone`
- `ACControl` (AC Unit Controller / Air Condition)
- access/keypad-style controls with `access`/`granted` and `wrongCode`/`denied` states (exposed as binary sensors)

Unsupported block types are still exposed, when possible, as disabled-by-default diagnostic sensors or binary sensors based on their raw states.

`Webpage` controls that look like intercom "system schema" pages are exposed as diagnostic-only entities (disabled by default), because they are configuration/visualization links rather than runtime sensor values.

## LoxAPP3 section coverage

Based on the current integration parser, the following `LoxAPP3.json` sections are consumed directly:

- `msInfo`
- `lastModified`
- `controls` (including nested `subControls`)
- `rooms`
- `cats`
- `mediaServer` (audio server metadata/state fallback for `media_player`)

The following documented sections are currently not mapped to dedicated Home Assistant entities:

- `globalStates`
- `operatingModes`
- `weatherServer`
- `times`
- `caller`
- `autopilot`
- `messageCenter`

## Important scope

This version targets modern Loxone servers (Miniserver, Miniserver Go, Miniserver Compact) with local TLS/WSS enabled. Discovery detects legacy units, but setup intentionally refuses insecure legacy mode because full real-time support without a verified crypto path would be brittle.

## Supported Loxone server software versions

- minimum supported software version: `10.2`
- setup validates the detected server software version during initial configuration
- unsupported versions are blocked with a dedicated config-flow error

## Installation with HACS

1. Open HACS.
2. Add this repository as a custom repository of type `Integration`.
3. Install `Loxone`.
4. Restart Home Assistant.
5. Add the integration from `Settings -> Devices & Services`.
6. In manual host mode you can use either:
   - local host/IP (for example `192.0.2.10`), or
   - Cloud DNS form based on MAC (`02AABBCCDDEE` or `https://dns.loxonecloud.com/02AABBCCDDEE`).


## Configuration flow

The integration first asks for setup mode and startup options:

- automatic discovery or manual host setup
- mood selection entities for `LightControllerV2`
- individual child light entities inside one `LightController`
- optional automatic creation of suggested automations

If automatic discovery is selected, the next step asks for:

- Loxone username
- Loxone password

It then scans the local network for one or more Loxone servers. If more than one is found, you choose the correct device. If discovery fails, a manual host fallback is offered.

If manual setup is selected, you provide host, port, credentials, and TLS verification directly.

## Services

### `loxone_home_assistant.send_command`

Send a command to a specific `uuidAction`.

Fields:

- `entry_id` optional when only one Loxone entry exists
- `uuid_action`
- `command`

### `loxone_home_assistant.send_raw_command`

Send a raw Loxone command over the authenticated WebSocket session.

Fields:

- `entry_id` optional when only one Loxone entry exists
- `command`

### `loxone_home_assistant.send_tts`

Send text-to-speech to a specific Loxone control UUID (for example Intercom or AudioZone).

Fields:

- `entry_id` optional when only one Loxone entry exists
- `uuid_action`
- `message`
- `volume` optional 0-100

### `loxone_home_assistant.call_intercom_function`

Trigger a child function assigned to an Intercom block (for example `Open`, `Close`, `Open and Pull`).

Fields:

- `entry_id` optional when only one Loxone entry exists
- `uuid_action` parent Intercom UUID action
- `function` function selector:
  - child function name (for example `Open`)
  - full child `uuidAction`
  - numeric suffix/index (for example `1`)

### `loxone_home_assistant.call_intercom_command`

Send a validated Intercom Gen2 command directly to the parent Intercom control.

Fields:

- `entry_id` optional when only one Loxone entry exists
- `uuid_action` parent Intercom UUID action
- `command` supported command name:
  - `answer`
  - `playTts` (alias `tts`)
  - `mute` (`0` unmute, `1` mute)
  - `setAnswers` (aliases: `setanswer`, `setallanswer`)
  - `setvideosettings`, `setallvideosettings`
  - `setframerate`, `setresolution`
  - `getnumberbellimages`, `setnumberbellimages`
- `arguments` optional:
  - slash-separated string (for example `0/20/1`, `Package box/Call owner`)
  - or YAML list (recommended when text contains special characters)

Examples:

```yaml
service: loxone_home_assistant.call_intercom_command
data:
  uuid_action: intercom-v2-uuid-action
  command: answer
```

```yaml
service: loxone_home_assistant.call_intercom_command
data:
  uuid_action: intercom-v2-uuid-action
  command: mute
  arguments: "1"
```

```yaml
service: loxone_home_assistant.call_intercom_command
data:
  uuid_action: intercom-v2-uuid-action
  command: setanswers
  arguments: "Leave package at box/Call owner"
```

## Logging and troubleshooting

- integration logger namespace: `custom_components.loxone_home_assistant`
- connection logs are transition-based:
  - one info log when connection becomes unavailable
  - one info log when connection is restored
- to inspect protocol/entity mapping in detail, enable debug logs in Home Assistant:

```yaml
logger:
  logs:
    custom_components.loxone_home_assistant: debug
```

## Intercom automation events

The integration fires Home Assistant bus events with type:

- `loxone_home_assistant_intercom_event`

Event payload includes:

- `serial`
- `uuid_action`
- `control_name`
- `control_type`
- `room`
- `category`
- `state_name`
- `event` (for example `doorbell`, `call`, `proximity`, `light`)
- `value`

Events are emitted on rising edges only (for example `0 -> 1`).

## Intercom live call scope

Home Assistant can reliably expose the intercom video stream and control/state/event automation, but full two-way live audio/video calling UX depends on vendor-specific mobile apps and codecs that are not standardized in HA entities.

This integration therefore focuses on:

- live video preview (`camera`)
- ring/call/proximity event and state automation
- TTS command dispatch
- intercom light control (when the block publishes compatible states)

## Development notes

- local push updates use `jdev/sps/enablebinstatusupdate`
- state structure comes from `data/LoxAPP3.json`
- setup is based on Home Assistant config flow conventions and Loxone token auth over secure WebSocket

## CI

This repository includes a GitHub Actions workflow in `.github/workflows/ci.yml`.
It validates the integration by:

- running unit tests against fixtures and protocol samples only
- installing the pinned Home Assistant runtime
- checking JSON metadata files
- importing all integration modules
- compiling the Python sources

## Secrets safety

- GitHub Actions in this repository do not use real Loxone credentials.
- Automated tests run on static fixtures and mocked protocol payloads only.
- Local secret files such as `.env`, `secrets.yaml`, and `local_test_credentials.json` are ignored by git.
- If you ever want to test against a real Loxone server, use GitHub Actions `Secrets` on a self-hosted runner inside your network. Do not commit credentials into the repository.

## Support

If this project is useful to you, you can support development here:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/szymonrybka)
