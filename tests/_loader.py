"""Helpers for loading selected integration modules without Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
import types
from enum import IntFlag, StrEnum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_ROOT = ROOT / "custom_components"
INTEGRATION_ROOT = COMPONENTS_ROOT / "loxone_home_assistant"


def install_media_player_stubs() -> None:
    """Provide the media player API used by isolated platform tests."""
    media_player = sys.modules["homeassistant.components.media_player"]

    if "homeassistant.exceptions" not in sys.modules:
        exceptions = types.ModuleType("homeassistant.exceptions")

        class HomeAssistantError(Exception):
            pass

        class ServiceValidationError(HomeAssistantError):
            pass

        exceptions.HomeAssistantError = HomeAssistantError
        exceptions.ServiceValidationError = ServiceValidationError
        sys.modules["homeassistant.exceptions"] = exceptions

    class MediaPlayerEntity:
        pass

    class MediaPlayerDeviceClass(StrEnum):
        SPEAKER = "speaker"

    class MediaPlayerState(StrEnum):
        OFF = "off"
        IDLE = "idle"
        PAUSED = "paused"
        PLAYING = "playing"

    class MediaPlayerEntityFeature(IntFlag):
        PAUSE = 1
        SEEK = 2
        VOLUME_SET = 4
        VOLUME_MUTE = 8
        PREVIOUS_TRACK = 16
        NEXT_TRACK = 32
        TURN_ON = 128
        TURN_OFF = 256
        PLAY_MEDIA = 512
        VOLUME_STEP = 1024
        SELECT_SOURCE = 2048
        STOP = 4096
        PLAY = 16384
        SHUFFLE_SET = 32768
        BROWSE_MEDIA = 131072
        REPEAT_SET = 262144

    class MediaType(StrEnum):
        MUSIC = "music"

    class MediaClass(StrEnum):
        APP = "app"
        DIRECTORY = "directory"
        MUSIC = "music"

    class RepeatMode(StrEnum):
        ALL = "all"
        OFF = "off"
        ONE = "one"

    media_player.BrowseMedia = types.SimpleNamespace
    media_player.MediaClass = MediaClass
    media_player.MediaPlayerDeviceClass = MediaPlayerDeviceClass
    media_player.MediaPlayerEntity = MediaPlayerEntity
    media_player.MediaPlayerEntityFeature = MediaPlayerEntityFeature
    media_player.MediaPlayerState = MediaPlayerState
    media_player.MediaType = MediaType
    media_player.RepeatMode = RepeatMode


def load_integration_module(module_name: str):
    """Load one module from the integration without importing its HA entrypoint."""
    _ensure_package("custom_components", COMPONENTS_ROOT)
    _ensure_package("custom_components.loxone_home_assistant", INTEGRATION_ROOT)

    relative_name = module_name.rsplit(".", 1)[-1]
    module_path = INTEGRATION_ROOT / f"{relative_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot create module spec for {module_name}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
