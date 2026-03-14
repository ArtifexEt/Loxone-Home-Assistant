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
- exposes `AudioZone`/`AudioZoneV2` as `media_player` with source selection, seek/progress, shuffle/repeat and TTS (`AudioZoneV2`)
- adds hub-level maintenance actions (for example server restart)

## Supported platforms

- `switch`
- `light`
- `cover`
- `climate`
- `sensor`
- `binary_sensor`
- `button`
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
- `PowerSupply`
- `PowerSupplyV2`
- `TextState`
- `Meter`
- `Slider`
- `TextInput`
- `AudioZone`
- `AudioZoneV2`
- access/keypad-style controls with `access`/`granted` and `wrongCode`/`denied` states (exposed as binary sensors)

Unsupported block types are still exposed, when possible, as disabled-by-default diagnostic sensors or binary sensors based on their raw states.

## Cross-check against the official Config 8 structure reference

Compared with the official Loxone Config 8 structure document
([EN_KB_Diagram_Config8_API_Structure.pdf](https://www.loxone.com/enen/wp-content/uploads/sites/3/2016/10/EN_KB_Diagram_Config8_API_Structure.pdf)),
Loxone exposes additional control types that are not yet mapped to dedicated Home Assistant entity models in this integration, for example:

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

Note: that document targets Loxone Config 8.0 (2016). Modern configurations can include newer control variants that are outside the Config 8 scope.

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

The integration first asks for:

- Loxone username
- Loxone password

It then scans the local network for one or more Loxone servers. If more than one is found, you choose the correct device. If discovery fails, a manual host fallback is offered.

In integration options you can also enable:

- mood selection entities for `LightControllerV2`
- individual child light entities inside one `LightController`

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
