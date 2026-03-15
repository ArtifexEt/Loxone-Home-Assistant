"""Cover platform for Loxone."""

from __future__ import annotations

import re
import unicodedata

try:
    from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
except ImportError:  # pragma: no cover - fallback for lightweight test stubs
    from homeassistant.components.cover import CoverEntity, CoverEntityFeature

    CoverDeviceClass = None  # type: ignore[assignment]
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COVER_CONTROL_TYPES
from .entity import LoxoneEntity, coerce_float
from .runtime import entry_bridge

NON_ALNUM_LABEL_RE = re.compile(r"[^a-z0-9]+")
CURTAIN_HINTS = {
    "curtain",
    "curtains",
    "drape",
    "drapes",
    "drapery",
    "firana",
    "firanki",
    "zaslona",
    "zaslony",
    "zacienienie",
}
BLIND_HINTS = {
    "blind",
    "blinds",
    "jalousie",
    "jaluzja",
    "jaluzje",
    "roleta",
    "rolety",
    "roller",
    "shutter",
    "shutters",
}
CURTAIN_HINT_SUBSTRINGS = {
    "curtain",
    "drape",
    "firan",
    "zaslon",
    "zacien",
}
BLIND_HINT_SUBSTRINGS = {
    "blind",
    "jalous",
    "jaluz",
    "rolet",
    "roller",
    "shutter",
    "lamell",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge = entry_bridge(hass, entry)
    entities = [
        LoxoneCoverEntity(bridge, control)
        for control in bridge.controls
        if control.type in COVER_CONTROL_TYPES
        and not _is_updownleftright_analog(control)
    ]
    async_add_entities(entities)


class LoxoneCoverEntity(LoxoneEntity, CoverEntity):
    """Representation of a Loxone blind or shutter."""

    @property
    def device_class(self):
        if self.control.type != "Jalousie":
            return None
        return _detect_jalousie_device_class(self.control)

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )
        if self.control.type == "Jalousie":
            features |= CoverEntityFeature.SET_POSITION
            if _supports_jalousie_tilt(self.control):
                features |= (
                    CoverEntityFeature.OPEN_TILT
                    | CoverEntityFeature.CLOSE_TILT
                    | CoverEntityFeature.STOP_TILT
                    | CoverEntityFeature.SET_TILT_POSITION
                )
        return features

    @property
    def current_cover_position(self) -> int | None:
        position = coerce_float(self.first_state_value("position", "targetPosition"))
        if position is None:
            return None
        if self.control.type == "Jalousie":
            if 0 <= position <= 1:
                position *= 100
            return max(0, min(100, round(100 - position)))
        if 0 <= position <= 1:
            return max(0, min(100, round(position * 100)))
        return max(0, min(100, round(position)))

    @property
    def current_cover_tilt_position(self) -> int | None:
        if not _supports_jalousie_tilt(self.control):
            return None
        tilt = coerce_float(
            self.first_state_value("shadePosition", "targetPositionLamelle")
        )
        if tilt is None:
            return None
        if 0 <= tilt <= 1:
            tilt *= 100
        return max(0, min(100, round(100 - tilt)))

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    async def async_open_cover(self, **kwargs) -> None:
        if self.control.type == "Gate":
            await self.bridge.async_send_action(self.control.uuid_action, "open")
            return
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "UpOn")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "FullUp")

    async def async_close_cover(self, **kwargs) -> None:
        if self.control.type == "Gate":
            await self.bridge.async_send_action(self.control.uuid_action, "close")
            return
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "DownOn")
            return
        await self.bridge.async_send_action(self.control.uuid_action, "FullDown")

    async def async_stop_cover(self, **kwargs) -> None:
        if self.control.type == "UpDownLeftRight":
            await self.bridge.async_send_action(self.control.uuid_action, "UpOff")
            await self.bridge.async_send_action(self.control.uuid_action, "DownOff")
            return
        try:
            await self.bridge.async_send_action(self.control.uuid_action, "stop")
        except Exception as err:
            from .bridge import LoxoneConnectionError

            if not isinstance(err, LoxoneConnectionError):
                raise
            # Compatibility fallback for controllers exposing directional stop commands only.
            await self.bridge.async_send_action(self.control.uuid_action, "UpOff")
            await self.bridge.async_send_action(self.control.uuid_action, "DownOff")

    async def async_set_cover_position(self, **kwargs) -> None:
        if self.control.type != "Jalousie":
            return
        target = _clamp_percent(kwargs["position"])
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"manualPosition/{100 - target}",
        )

    async def async_open_cover_tilt(self, **kwargs) -> None:
        if not _supports_jalousie_tilt(self.control):
            return
        await self.bridge.async_send_action(self.control.uuid_action, "manualLamelle/0")

    async def async_close_cover_tilt(self, **kwargs) -> None:
        if not _supports_jalousie_tilt(self.control):
            return
        await self.bridge.async_send_action(self.control.uuid_action, "manualLamelle/100")

    async def async_stop_cover_tilt(self, **kwargs) -> None:
        if not _supports_jalousie_tilt(self.control):
            return
        await self.bridge.async_send_action(self.control.uuid_action, "stop")

    async def async_set_cover_tilt_position(self, **kwargs) -> None:
        if not _supports_jalousie_tilt(self.control):
            return
        target = _clamp_percent(kwargs["tilt_position"])
        await self.bridge.async_send_action(
            self.control.uuid_action,
            f"manualLamelle/{100 - target}",
        )


def _is_updownleftright_analog(control) -> bool:
    if control.type != "UpDownLeftRight":
        return False
    normalized_states = {state_name.strip().casefold() for state_name in control.states}
    return any(state_name in normalized_states for state_name in ("value", "position", "actual"))


def _supports_jalousie_tilt(control) -> bool:
    if control.type != "Jalousie":
        return False
    if _has_jalousie_tilt_states(control):
        return True
    # In Loxone structure files animation=0 can denote venetian blinds with lamellas.
    return coerce_float(control.details.get("animation")) == 0.0


def _has_jalousie_tilt_states(control) -> bool:
    if control.type != "Jalousie":
        return False
    normalized_states = {state_name.strip().casefold() for state_name in control.states}
    return any(
        state_name in normalized_states
        for state_name in ("shadeposition", "targetpositionlamelle")
    )


def _detect_jalousie_device_class(control):
    # Loxone icon naming usually distinguishes textile curtains from blinds.
    icon_based = _device_class_from_text(control.icon)
    if icon_based is not None:
        return icon_based

    if _has_cover_hint(control, CURTAIN_HINTS, CURTAIN_HINT_SUBSTRINGS):
        return _cover_device_class("CURTAIN")

    if _has_cover_hint(control, BLIND_HINTS, BLIND_HINT_SUBSTRINGS):
        return _cover_device_class("BLIND")

    if _has_jalousie_tilt_states(control):
        return _cover_device_class("BLIND")

    # For generic shading controls we default to curtain-like semantics.
    return _cover_device_class("CURTAIN")


def _cover_device_class(name: str):
    if CoverDeviceClass is None:
        return name.casefold()
    return getattr(CoverDeviceClass, name, name.casefold())


def _normalize_cover_label(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(
        char for char in normalized.casefold() if not unicodedata.combining(char)
    )
    collapsed = NON_ALNUM_LABEL_RE.sub(" ", ascii_only)
    return " ".join(part for part in collapsed.split() if part)


def _device_class_from_text(value: str | None):
    normalized = _normalize_cover_label(value)
    if not normalized:
        return None
    compact = normalized.replace(" ", "")
    curtain_match = _matches_hint(normalized, compact, CURTAIN_HINTS, CURTAIN_HINT_SUBSTRINGS)
    blind_match = _matches_hint(normalized, compact, BLIND_HINTS, BLIND_HINT_SUBSTRINGS)
    if curtain_match and not blind_match:
        return _cover_device_class("CURTAIN")
    if blind_match and not curtain_match:
        return _cover_device_class("BLIND")
    return None


def _has_cover_hint(control, hints: set[str], substrings: set[str]) -> bool:
    candidates = (
        control.name,
        control.display_name,
        control.room_name,
        control.category_name,
    )
    for value in candidates:
        normalized = _normalize_cover_label(value)
        if not normalized:
            continue
        compact = normalized.replace(" ", "")
        if _matches_hint(normalized, compact, hints, substrings):
            return True
    return False


def _matches_hint(
    normalized_value: str,
    compact_value: str,
    hints: set[str],
    substrings: set[str],
) -> bool:
    words = set(normalized_value.split())
    if words & hints:
        return True
    return any(fragment in compact_value for fragment in substrings)


def _clamp_percent(value: int | float) -> int:
    return max(0, min(100, round(float(value))))
