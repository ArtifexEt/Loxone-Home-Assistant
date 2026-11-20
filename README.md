# Loxone

[![CI](https://github.com/ArtifexEt/Loxone-Home-Assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtifexEt/Loxone-Home-Assistant/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant-community-store&logoColor=white)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-18BCF2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)

Custom HACS integration for local control of Loxone Miniserver Gen 1 and Gen 2, Miniserver Go, and Miniserver Compact from Home Assistant.

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
- connects locally over WebSocket using WSS or Loxone command encryption
- imports the Loxone structure from `LoxAPP3.json`
- keeps two-way state sync between Home Assistant and Loxone
- checks for project changes after reconnecting to the Miniserver and every five minutes while connected,
  then reloads the integration to discover new controls and update existing entities
- exposes common Loxone blocks as Home Assistant entities
- exposes Intercom/IntercomV2 (and DoorController/DoorStation variants) as dedicated doorbell entities:
  - camera stream + snapshot preview when available
  - binary sensors for ring, call, proximity, and intercom light state
  - Intercom Gen2 action buttons (`Stop Ringing`, `Mute Doorbell`, `Unmute Doorbell`)
  - intercom history sensor based on `lastBellEvents` (latest event timestamp + recent image URLs)
  - Home Assistant bus events (`loxone_home_assistant_intercom_event`) for automation triggers
- exposes access/keypad-style activity events as Home Assistant bus events (`loxone_home_assistant_access_event`) so automations can react to granted/denied attempts (for example wrong PIN)
- exposes `AudioZone`/`AudioZoneV2`/`CentralAudioZone` as `media_player` with source selection, browseable source library, seek/progress, shuffle/repeat, TTS and event/command passthrough via `play_media`
- exposes Central Audio group metadata on `media_player` (linked zone names, UUIDs, entity ids when available)
  - adds optional native audio TTS helpers per zone: `text.<audio>_tts_message`, `number.<audio>_tts_volume`, `button.<audio>_send_tts`
- exposes `PresenceDetector` as:
  - `binary_sensor` for presence/motion
  - `sensor` for illuminance (`lx`) and sound level (`dB`)
- exposes `Tracker` as event/history sensors for log-like states (with `log_entry_count`, `latest_log_entry`, and `log_entries` attributes on event sensors such as `entries`)
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
- `weatherServer`
- `times`
- `caller`
- `autopilot`
- `messageCenter`

## Miniserver connection

TLS-capable Miniservers use secure WebSocket (WSS). Miniserver Gen 1 uses Loxone command encryption over WebSocket on its configured HTTP port. Gen 1 firmware must provide a valid Loxone-signed chain through `jdev/sys/getcertificate`. The integration verifies it before establishing the encrypted session. That encryption protects authentication and commands. State events and some responses can travel unencrypted, so keep the Gen 1 connection on a trusted local network.

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
   - Remote Connect form based on the Miniserver serial (`02AABBCCDDEE` or `https://connect.loxonecloud.com/02AABBCCDDEE`).


## Configuration flow

Choose **Search the local network** to discover a Miniserver or **Enter a host and port** to connect directly. Both paths ask for your Loxone username and password. If discovery finds multiple servers, choose the one to connect.

For a Miniserver Gen 1 with a custom port, select **Enter a host and port**. Enter the hostname or IP in **Host** and the configured HTTP port in **Port**, for example `7778`. Manual setup is available even when a Gen 2 Miniserver is already configured.

On TLS-capable Miniservers, Intercom video uses the configured Loxone user by default. With Gen 1, Miniserver media requests are anonymous. A direct Intercom endpoint can use its own separate credentials.

If your Intercom camera stream returns `401 Unauthorized`, open integration `Options` and set dedicated Intercom video credentials (some Intercom models use separate video-module login data).

Loxone 17.3 adds optional email MFA for Miniserver users and groups. Loxone disables username/password web services for accounts covered by that policy. Check the account and group policy when HTTP media requests stop authenticating after an upgrade. [Loxone Config changelog](https://www.loxone.com/enen/support/changelog-config/)

After successful connection and validation, the final step asks for startup options:

- mood selection entities for `LightControllerV2` as a separate `select` helper (enabled by default)
- individual child light entities inside one `LightController` (enabled by default)
- optional automatic creation of suggested automations
- optional Loxone icon pictures for entities (disabled by default)

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
- `volume` optional 0-100 (used for AudioZone; Intercom TTS ignores this value)

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
  - `answer` stops the current ringing (with aliases `accept`, `answercall`, `pickup`)
  - `playTts` (alias `tts`)
  - `mute` controls the doorbell sound (`0` unmutes and `1` mutes, with aliases `muteMic`, `unmute`, `unmuteMic`)
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

## Intercom UI in Home Assistant (live video + TTS)

Use parent Intercom `uuid_action` (you can copy it from any Intercom entity attributes, for example `camera.<name>_video`).

The integration now creates native Intercom TTS controls automatically (per Intercom):

- `text.<intercom>_tts_message`
- `number.<intercom>_tts_volume`
- `button.<intercom>_send_tts`

Pressing `Send TTS` sends only message text to the parent Intercom `uuid_action`.

You can still call service directly if needed:

```yaml
service: loxone_home_assistant.send_tts
data:
  uuid_action: intercom-v2-uuid-action
  message: "Kurier jest pod drzwiami"
  volume: 60
```

Lovelace card example (`camera_view: live` is important for video stream):

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: camera.furtka_video
    camera_view: live
    show_state: false
    show_name: true
  - type: entities
    entities:
      - binary_sensor.furtka_doorbell
      - sensor.furtka_history
      - text.furtka_tts_message
      - number.furtka_tts_volume
      - button.furtka_send_tts
```

The camera can show a live JPEG or MJPEG feed when the controller provides a compatible HTTP endpoint. History images are built
from `lastBellEvents` and fetched from Miniserver `camimage/{uuidAction}/{timestamp}`.
The `select.<name>_history_photo` entity is enabled by default so you can switch the camera
preview between `Live` and historical snapshots. When the Intercom also exposes an
`eventHistoryUrl` payload, the selector includes the full history returned by that endpoint.

## Audio TTS UI in Home Assistant

For `AudioZone`, `AudioZoneV2` and `CentralAudioZone`, the integration creates native helpers:

- `text.<audio>_tts_message`
- `number.<audio>_tts_volume`
- `button.<audio>_send_tts`

Pressing `Send TTS` uses the same routing as `media_player.play_media(..., media_content_type: tts)`,
including routing through the matched Loxone media server when applicable.

### Optional MJPEG -> HLS wrapper

If a client/player has trouble with MJPEG, you can wrap MJPEG to HLS locally:

```bash
python3 scripts/mjpeg_hls_wrapper.py \
  --input-url "http://intercom.local/mjpg/video.mjpg" \
  --bind 127.0.0.1 \
  --port 8899
```

The script prints an HLS URL (for example `http://127.0.0.1:8899/stream.m3u8`).
You can use this URL as a browser-friendly stream endpoint.

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

## Automation events

The integration fires Home Assistant bus events with type:

- `loxone_home_assistant_intercom_event`
- `loxone_home_assistant_access_event`

Event payload includes:

- `serial`
- `uuid_action`
- `control_name`
- `control_type`
- `room`
- `category`
- `state_name`
- `event` (for example `doorbell`, `call`, `proximity`, `light`, `access_denied`, `access_granted`, `access_activity`)
- `value`

Intercom events are emitted on rising edges only (for example `0 -> 1`).
Access/keypad events are emitted on rising edges for boolean-like states and on value change for log/text-like states.

## Intercom live call scope

Intercom Gen 2 uses WebRTC for live video and two-way audio in the Loxone app. Use the Loxone app or web interface for calls. The Home Assistant integration supports cameras that expose compatible HTTP media, doorbell and proximity events, text-to-speech, and available light controls. The `answer` command stops the bell, and `mute` controls the doorbell mute state. [Loxone Intercom documentation](https://www.loxone.com/enus/kb/intercom-2/) · [Network protocols](https://updatefiles.loxone.com/KnowledgeBase/Online/Common/Documents/Loxone-Required-Ports-Domains.pdf)

## Development notes

- local push updates use `jdev/sps/enablebinstatusupdate`
- state structure comes from `data/LoxAPP3.json`
- setup uses Home Assistant config flows and Loxone token authentication over WSS or an encrypted Gen 1 command session

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
