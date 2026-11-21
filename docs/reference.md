# Loxone technical reference

[Back to the installation guide](../README.md)

- [Compatibility and connection](#compatibility-and-connection)
- [Entity mapping](#entity-mapping)
- [Home Assistant actions](#home-assistant-actions)
- [Automation events](#automation-events)
- [Intercom media](#intercom-media)
- [Audio and announcements](#audio-and-announcements)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Compatibility and connection

The integration supports Home Assistant **2025.1.0 or later** and checks for Miniserver firmware **10.2 or later** during setup. It supports Miniserver Gen 1 and Gen 2, Miniserver Go, and Miniserver Compact. Individual features depend on the firmware, the control's project configuration, and the Home Assistant version.

| Connection | Configuration | Transport |
| --- | --- | --- |
| Local discovery | Choose **Search the local network** and supply your Miniserver credentials. Select a Miniserver if several are found. | WSS on compatible hardware or an encrypted Gen 1 command session |
| Manual address | Choose **Enter a host and port**. Use a local hostname or address such as `miniserver.example` or `192.0.2.10`. | WSS on the configured HTTPS port, normally 443 |
| Miniserver Gen 1 | Enter the local hostname or address and the Miniserver's configured HTTP port, such as `7778`. | WebSocket with Loxone command encryption |
| Loxone Remote Connect | Enter the Miniserver serial or its `connect.loxonecloud.com` URL in the manual host field. | WSS through a validated Loxone relay |

Gen 1 requires firmware that provides a valid Loxone-signed certificate chain at `jdev/sys/getcertificate`. The integration verifies the chain before exchanging session keys. Gen 1 encryption protects authentication and commands. State events and some responses can travel in clear text, so use a trusted local network.

The local HTTPS form provides a **Verify TLS certificate** setting. Enable it when Home Assistant trusts the Miniserver's certificate. Remote Connect always requires TLS verification.

Loxone Config 17.3 introduces optional email MFA for Miniserver users and groups. The policy disables username/password web services for covered accounts and can affect integrations that fetch media over HTTP. Review the account and group policy if media authentication changes after an upgrade. [Loxone Config changelog](https://www.loxone.com/enen/support/changelog-config/) · [Miniserver protocol](https://www.loxone.com/enen/wp-content/uploads/sites/3/2026/04/1700_Communicating-with-the-Miniserver.pdf)

## Entity mapping

The following table describes the mappings in this source tree. Your project determines which entities are created and which operations they expose. Check the control's available states, details and command support when adding an automation.

| Area | Loxone controls and Home Assistant entities |
| --- | --- |
| Lighting | `Dimmer`, `ColorPicker`, `ColorPickerV2`, `LightsceneRGB`, `LightController` and `LightControllerV2` use `light`. Scenes and moods can appear as separate `select` entities. Child lights can be exposed individually. |
| Switches and inputs | `Switch`, `TimedSwitch` and `CarCharger` use `switch` as applicable. `Pushbutton` variants use `button`. `Slider`, `ValueSelector` and analog `UpDownLeftRight` use `number`. `TextInput` uses `text`. |
| Shading and openings | `Jalousie`, `Window`, `Gate` and digital `UpDownLeftRight` use `cover`. The relevant controls also expose actions for shading, partial opening, pulses or automatic control. `WindowMonitor` exposes opening states. |
| Temperature and air | `IRoomController`, `IRoomControllerV2`, `ACControl`, `PoolController` and `Sauna` use `climate`. `Ventilation` exposes configured timer profiles, automatic mode, readings and intensity limits that Loxone logic leaves adjustable. `Heatmixer` exposes readings. |
| Alarm and access | `Alarm` uses `alarm_control_panel`, with state available on controls protected by a separate visualization password. Away arming follows the Loxone delay. `AlarmChain`, `AlarmClock`, `SmokeAlarm` and access-like controls expose buttons, sensors or binary sensors as applicable. |
| Intercom | `Intercom`, `IntercomV2`, door controller and door station variants can expose cameras, doorbell activity, media history, action buttons and TTS. Camera and history entities depend on the connectivity and data available from each device. |
| Energy and diagnostics | `Meter`, `Fronius`, `EnergyFlowMonitor`, `Wallbox2`, `PowerSupply`, `PowerSupplyV2`, `PowerUnit` and `Hourcounter` expose available readings. `Wallbox2` exposes status and metering. `EnergyFlowMonitor` exposes root-level state from the project. |
| Presence and status | `PresenceDetector` can expose presence, illuminance and sound level. `InfoOnlyAnalog`, `InfoOnlyDigital`, `InfoOnlyText`, `TextState`, `Daytimer`, `Tracker` and `Webpage` expose applicable readings or diagnostics. A `Tracker` log sensor can expose `log_entry_count`, `latest_log_entry` and `log_entries`. |
| Audio and media controls | `AudioZone`, `AudioZoneV2` and `CentralAudioZone` use `media_player`. `Radio` exposes an output selector. `Remote` uses `remote` for its activities and available keys. |

Existing alarm and media-controller switch entities keep their registry settings. Some unsupported blocks expose raw diagnostic sensors or binary sensors, disabled by default. A `Webpage` with a system diagram can also appear as a disabled diagnostic.

The parser reads `msInfo`, `lastModified`, `controls` and nested `subControls`, `rooms`, `cats`, and `mediaServer` from `LoxAPP3.json`. Sections such as `globalStates`, `weatherServer`, `times`, `caller`, `autopilot`, and `messageCenter` remain outside the dedicated entity mappings. [Loxone structure file specification](https://www.loxone.com/enen/wp-content/uploads/sites/3/2026/04/1700_Structure-File.pdf)

## Home Assistant actions

Find a control's `uuid_action` in the attributes of one of its Home Assistant entities. Use the parent control UUID for Intercom actions. Supply `entry_id` when more than one Loxone config entry is loaded.

| Action | Fields other than `entry_id` | Behavior |
| --- | --- | --- |
| `loxone_home_assistant.send_command` | `uuid_action`, `command` | Sends a control command. |
| `loxone_home_assistant.send_raw_command` | `command` | Sends a full command path on the authenticated WebSocket. |
| `loxone_home_assistant.send_tts` | `uuid_action`, `message`, optional `volume` | Speaks text through an Intercom or audio control. Audio volume uses 0–100 percent. Intercom uses its own volume configuration. |
| `loxone_home_assistant.call_intercom_function` | `uuid_action`, `function` | Pulses a child function selected by name, child UUID or numeric suffix/index. |
| `loxone_home_assistant.call_intercom_command` | `uuid_action`, `command`, optional `arguments` | Sends an Intercom Gen 2 command from the allowlist below. |

Discover actions in **Developer tools → Actions**. For example, to read CPU load and store the response for a later automation step, use the following script action.

```yaml
action: loxone_home_assistant.send_raw_command
data:
  command: dev/sys/cpu
response_variable: loxone_cpu
```

### Intercom command allowlist

Use an `arguments` list to preserve text that contains slashes. The video settings commands also accept slash-separated strings. Use integer IDs and values supported by the Miniserver firmware. [Loxone structure file specification](https://www.loxone.com/enen/wp-content/uploads/sites/3/2026/04/1700_Structure-File.pdf)

| Command | Arguments | Purpose |
| --- | --- | --- |
| `answer` | None | Stops the current ringing. Aliases include `accept`, `answercall`, and `pickup`. |
| `playTts` | One answer index | Plays a predefined answer. Alias `tts`. For custom text, use the separate `send_tts` action. |
| `mute` | `1` to mute, `0` to unmute | Controls the doorbell sound. `muteMic` and `microphoneMute` default to `1`. `unmute`, `unmuteMic` and `microphoneUnmute` default to `0`. |
| `setAnswers` | One or more consecutive answer texts | Stores predefined answers. Aliases include `setanswer` and `setallanswer`. |
| `setvideosettings` | Three integers | Sets video parameters for one stream. |
| `setallvideosettings` | Four integers | Sets video parameters for the available streams. |
| `setframerate`, `setresolution` | Two integers each | Changes the selected stream's frame rate or resolution. |
| `getnumberbellimages` | None | Reads the number of doorbell images. |
| `setnumberbellimages` | One integer | Changes the number of doorbell images. |

```yaml
action: loxone_home_assistant.call_intercom_command
data:
  uuid_action: "00000000-0000-0000-0000-000000000101"
  command: setAnswers
  arguments:
    - "Please leave the package in the box"
    - "Please call from the entrance"
```

## Automation events

Listen in **Developer tools → Events** before building an automation. Event payloads include `serial`, `uuid_action`, `control_name`, `control_type`, `room`, `category`, `state_name`, `event` and `value`.

| Event type | Values in `event` | Triggering behavior |
| --- | --- | --- |
| `loxone_home_assistant_intercom_event` | `doorbell`, `call`, `proximity`, `light` | Emitted when the corresponding state becomes active. |
| `loxone_home_assistant_access_event` | `access_granted`, `access_denied`, `access_activity` | Uses activation or text changes where available. Explicit grant and deny signals can repeat. |

This example uses a fictional control UUID and a Home Assistant notification. Replace the UUID with the one from your Intercom.

```yaml
alias: Front door bell
triggers:
  - trigger: event
    event_type: loxone_home_assistant_intercom_event
    event_data:
      uuid_action: "00000000-0000-0000-0000-000000000101"
      event: doorbell
actions:
  - action: persistent_notification.create
    data:
      title: Doorbell
      message: Someone rang the front entrance.
```

## Intercom media

The **Visit** gallery, language selector and **Photo preview** described here were introduced with Intercom Gen 2 support. [PR #4](https://github.com/ArtifexEt/Loxone-Home-Assistant/pull/4) · [First prerelease with these features](https://github.com/ArtifexEt/Loxone-Home-Assistant/releases/tag/v0.1.1b31)

### Live view and two-way audio

A locally paired Intercom Gen 2 exposes a separate **Live** entity on Home Assistant **2026.11 or later** when the integration can reach the Miniserver over HTTPS. The integration negotiates H.264 video through authenticated WebRTC signaling and loads its media dependencies when first needed. The **Photo preview** entity, called **Video** in some builds, serves still images and history. A live still can come from WebRTC when the Intercom has no compatible HTTP snapshot.

Enable **Two-way audio** in **Settings → System → Labs** and open Home Assistant over HTTPS in a browser. Open the **Live** camera and select **Start talking**. A tile that opens the live camera's **More info** dialog also works. iOS Companion can supply the microphone in a separate session, including when the app connects to Home Assistant over HTTP. [Home Assistant camera documentation](https://www.home-assistant.io/integrations/camera/#talking-through-a-camera) · [Loxone Intercom Gen 2](https://www.loxone.com/enus/kb/intercom-2/)

### Photo history and dashboard

- Open the Intercom device under **Settings → Devices & Services → Loxone → Devices** and select **Visit**. The Gen 2 gallery shows 18 thumbnails per page. Select a thumbnail to open the full image and download a JPEG.
- Use **History photo** on the device page to change the **Photo preview** still. Select **Live** to return to the current view. For compatible legacy controls, history can come from `lastBellEvents` with images at `camimage/{uuidAction}/{timestamp}` or from a controller's HTTP history endpoint.
- Use **Configure → Intercom language** on the Miniserver entry to choose English, Polish, German, or the Home Assistant language. The project still supplies the device name. A **Doorbell mute** switch and **Device Status** are available when the Intercom reports the corresponding states.

To embed the gallery, enable Advanced Mode in your Home Assistant profile if needed. Add `/loxone_home_assistant/intercom-gallery.js` as a **JavaScript module** under **Settings → Dashboards → Resources**, then add a manual card.

```yaml
type: custom:loxone-intercom-gallery
entity: camera.example_intercom_video
```

The gallery requires Home Assistant read access to the selected camera. The [dashboard YAML example](../examples/intercom-dashboard.yaml) includes two views for live video and history. Copy the views into a dashboard's **Raw configuration editor** and replace the example entity IDs.

| Example ID | Entity to select on the Intercom device |
| --- | --- |
| `camera.example_intercom_live` | Live |
| `camera.example_intercom_video` | Photo preview |
| `binary_sensor.example_intercom_doorbell` | Doorbell signal |
| `text.example_intercom_tts_message` | TTS Message |
| `button.example_intercom_send_tts` | Send TTS |

For a compatible HTTP stream, use a Home Assistant Picture Entity card with `camera_view: live` and the **Video** or **Photo preview** camera entity. TLS-capable Miniservers can use the configured Loxone credentials for Miniserver media. Miniserver media requests on Gen 1 use anonymous access. Direct video endpoints can use their own credentials, set under the integration's **Configure** options.

## Audio and announcements

Audio controls depend on the model, firmware and integration release. Check the release notes for the features in your installed version.

Audio zones can expose source selection, a browseable source list and metadata for linked zones. Transport controls, seeking, shuffle, repeat, volume limits and related features depend on the zone and firmware. Where Loxone allows app control, Audio Player blocks can also expose Bluetooth. Original Music Server zones can expose startup and event volume settings when the project publishes the parameters.

Use the **TTS Message** and **Send TTS** entities for an Intercom or a supported audio zone. Audio zones can also expose **TTS Volume**. The TTS helpers are drafts held in the integration's runtime. For saved or reusable announcements, store the text in an automation or script. Intercom builds with PR #4 accept up to 255 characters in the dashboard text field.

```yaml
action: loxone_home_assistant.send_tts
data:
  uuid_action: "00000000-0000-0000-0000-000000000201"
  message: "pl|Drzwi wejściowe są otwarte"
  volume: 45
```

For an audio zone you can also use the Home Assistant media player action.

```yaml
action: media_player.play_media
target:
  entity_id: media_player.example_kitchen
data:
  media_content_type: tts
  media_content_id: "The laundry cycle is complete."
  extra:
    volume: 40
```

Central Audio forwards text announcements to the mapped member zones. An original Music Server with a configured local server address and zone ID can also play an event MP3 using `event_file` with a path relative to `Event_Sounds`, or a playlist using `event_playlist` with its name. Its event mechanism resumes the previous playback afterward. For an Audioserver or Miniserver Compact, trigger stored sounds through the Audio Player's custom-sound input in Loxone Config. [Audio Player](https://www.loxone.com/enen/kb/audio-player/) · [Music Server](https://www.loxone.com/enen/kb/loxone-music-server/)

Intercom Gen 2 speech synthesis requires internet access from Loxone. The Intercom uses its configured speaker volume. [Loxone Intercom Gen 2](https://www.loxone.com/enen/kb/intercom-2/)

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Miniserver missing from discovery | Use a manual address and the port configured in Loxone. Discovery looks for hosts on enabled Home Assistant network adapters. Verify that Home Assistant can reach the Miniserver. |
| Missing or disabled entities | Check that the Loxone account can see the control in the project and that the required states exist. Check disabled entities in Home Assistant and the integration's options for mood selectors, child lights and icon pictures. |
| Project changes are not visible yet | The integration checks the project's version after reconnecting and approximately every five minutes while connected. To import immediately, reload the integration entry. Builds containing PR #4 also provide **Refresh entities** on the Miniserver device. |
| Intercom video returns HTTP 401 | Set dedicated video credentials in the integration's **Configure** options for a direct video module. For Miniserver media on modern firmware, review the account and its MFA policy. |
| Microphone control is missing | Open the **Live** camera and check the Home Assistant version, the **Two-way audio** Labs setting, HTTPS, and microphone permissions. Confirm the Intercom is paired locally and the Miniserver uses HTTPS. |
| Archive temporarily unavailable | Check that your Home Assistant user can read the camera and that the Intercom and Miniserver can reach each other. The Gen 2 gallery can retain recently loaded thumbnails during a temporary archive connection failure. |
| An MJPEG client expects HLS | The optional [MJPEG wrapper](../scripts/mjpeg_hls_wrapper.py) can convert a stream locally. Keep the wrapper bound to loopback unless your deployment explicitly needs remote access. |

The Miniserver device includes diagnostics for available `dev/sys/numtasks`, `dev/sys/cpu`, `dev/sys/heap` and `dev/sys/ints` responses. **Refresh System Stats** runs the diagnostics on demand. **Restart Miniserver** restarts the controller, which interrupts its service.

Enable debug logging with the integration namespace when troubleshooting an issue.

```yaml
logger:
  logs:
    custom_components.loxone_home_assistant: debug
```

Before sharing logs or diagnostics, remove usernames, tokens, passwords, private network addresses, serial numbers, project names, event payloads and camera images.

## Development

The bridge uses Loxone token authentication over WSS or an encrypted Gen 1 session. It loads the project from `data/LoxAPP3.json` and subscribes to push updates with `jdev/sps/enablebinstatusupdate`. Structure checks compare `jdev/sps/LoxAPPversion3` with the loaded timestamp and confirm changes from the structure file before reloading.

Run tests from the repository root in a Python environment compatible with the Home Assistant version under test. The [CI workflow](../.github/workflows/ci.yml) supplies the pinned dependencies and runs fixture-based tests, validates JSON, imports integration modules and compiles sources.

```bash
python -m unittest discover -s tests
python -m compileall -q custom_components/loxone_home_assistant
```

Until 1.0.0, [the repository test workflow](../AGENTS.md) requires a clean reinstall for Home Assistant verification. Uninstall Loxone from the test instance, reinstall the build under test, and then verify entities, states and behavior. Keep hardware credentials in the test instance or a local secret store and use fictional fixtures for CI and screenshots.
