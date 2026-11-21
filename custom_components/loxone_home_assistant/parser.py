"""Parser for Loxone `LoxAPP3.json` files."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from .models import LoxoneControl, LoxoneMediaServer, LoxoneStateRef, LoxoneStructure
from .protocol import normalize_uuid
from .server_model import detect_server_model_from_mapping

_MAC_PLAIN_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
_MAC_SEPARATED_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def parse_structure(payload: Mapping[str, Any]) -> LoxoneStructure:
    """Parse the Loxone structure into flat control and state maps."""
    rooms = dict(_object_items(payload.get("rooms")))
    categories = dict(_object_items(payload.get("cats")))
    ms_info = _as_mapping(payload.get("msInfo"))
    server_model = detect_server_model_from_mapping(ms_info)

    controls: list[LoxoneControl] = []

    def visit(
        control_uuid: str,
        raw: Mapping[str, Any],
        parent: LoxoneControl | None = None,
    ) -> None:
        name = _safe_name(raw.get("name")) or control_uuid
        room_uuid = _safe_name(raw.get("room")) or (parent.room_uuid if parent else None)
        category_uuid = _safe_name(raw.get("cat")) or (parent.category_uuid if parent else None)
        room = rooms.get(room_uuid, {})
        category = categories.get(category_uuid, {})
        control = LoxoneControl(
            uuid=control_uuid,
            uuid_action=_safe_name(raw.get("uuidAction")) or control_uuid,
            name=name,
            type=_safe_name(raw.get("type")) or "Unknown",
            room_uuid=room_uuid,
            room_name=_safe_name(room.get("name")) if room else None,
            category_uuid=category_uuid,
            category_name=_safe_name(category.get("name")) if category else None,
            states=_coerce_control_state_map(raw),
            details=_coerce_details(raw),
            parent_uuid_action=parent.uuid_action if parent else None,
            path=(name,),
            is_secured=bool(raw.get("isSecured", False)),
            icon=(
                _coerce_icon_path(raw.get("defaultIcon"))
                or _coerce_icon_path(raw.get("image"))
                or _coerce_icon_path(category.get("image"))
                or _coerce_icon_path(room.get("image"))
            ),
        )
        controls.append(control)

        for sub_uuid, sub_raw in _object_items(raw.get("subControls")):
            visit(sub_uuid, sub_raw, parent=control)

    for control_uuid, raw_control in _object_items(payload.get("controls")):
        visit(control_uuid, raw_control)

    controls_by_action = {control.uuid_action: control for control in controls}
    _infer_parent_links_from_uuid_action(controls_by_action)
    _rebuild_paths_from_parent_links(controls_by_action)

    return LoxoneStructure(
        miniserver_name=_safe_name(ms_info.get("msName")) or f"Loxone {server_model}",
        server_model=server_model,
        serial=_safe_name(ms_info.get("serialNr")) or "unknown",
        loxapp_version=_safe_name(payload.get("lastModified")),
        controls=controls,
        controls_by_action=controls_by_action,
        states=_build_state_refs(controls),
        media_servers_by_uuid_action=_parse_media_servers(payload.get("mediaServer")),
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _object_items(value: Any) -> Iterator[tuple[str, Mapping[str, Any]]]:
    for key, item in _as_mapping(value).items():
        if isinstance(item, Mapping):
            yield key, item


def _coerce_state_map(raw_states: Any) -> dict[str, str]:
    return {
        name: normalize_uuid(state_uuid)
        for name, state_uuid in _as_mapping(raw_states).items()
        if isinstance(state_uuid, str) and state_uuid.strip()
    }


def _coerce_control_state_map(raw_control: Mapping[str, Any]) -> dict[str, str]:
    states = _coerce_state_map(raw_control.get("states"))
    # Some CentralAudioZone variants publish live UUIDs in events.
    for name, state_uuid in _coerce_state_map(raw_control.get("events")).items():
        states.setdefault(name, state_uuid)
    return states


def _coerce_details(raw_control: Mapping[str, Any]) -> dict[str, Any]:
    details = dict(_as_mapping(raw_control.get("details")))
    raw_secured_details = raw_control.get("securedDetails")
    if isinstance(raw_secured_details, Mapping):
        details["securedDetails"] = dict(raw_secured_details)
    elif raw_secured_details is True:
        details["securedDetails"] = True

    return details


def _safe_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_icon_path(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _infer_parent_links_from_uuid_action(
    controls_by_action: dict[str, LoxoneControl],
) -> None:
    """Infer parent links for flat exports where sub-controls use `parent/child` uuidAction."""
    for control in controls_by_action.values():
        if control.parent_uuid_action:
            continue
        parent_uuid_action = control.uuid_action.rpartition("/")[0]
        if parent_uuid_action in controls_by_action:
            control.parent_uuid_action = parent_uuid_action


def _rebuild_paths_from_parent_links(
    controls_by_action: dict[str, LoxoneControl],
) -> None:
    resolved_paths: dict[str, tuple[str, ...]] = {}
    visiting: set[str] = set()

    def resolve_path(control: LoxoneControl) -> tuple[str, ...]:
        if control.uuid_action in resolved_paths:
            return resolved_paths[control.uuid_action]
        if control.uuid_action in visiting:
            return control.path or (control.name,)

        visiting.add(control.uuid_action)
        parent = controls_by_action.get(control.parent_uuid_action or "")
        if parent is None or parent is control:
            path = control.path or (control.name,)
        else:
            path = resolve_path(parent) + (control.name,)
        visiting.remove(control.uuid_action)
        resolved_paths[control.uuid_action] = path
        return path

    for control in controls_by_action.values():
        if control.parent_uuid_action is None:
            continue
        control.path = resolve_path(control)


def _build_state_refs(controls: list[LoxoneControl]) -> dict[str, LoxoneStateRef]:
    states: dict[str, LoxoneStateRef] = {}
    for control in controls:
        for state_name, state_uuid in control.states.items():
            states[state_uuid] = LoxoneStateRef(
                control_uuid_action=control.uuid_action,
                control_name=control.display_name,
                control_type=control.type,
                state_name=state_name,
            )
    return states


def _parse_media_servers(raw_media_servers: Any) -> dict[str, LoxoneMediaServer]:
    parsed: dict[str, LoxoneMediaServer] = {}
    for media_server_uuid, raw_media_server in _object_items(raw_media_servers):
        fallback_uuid = _safe_name(media_server_uuid)
        uuid_action = _safe_name(raw_media_server.get("uuidAction")) or fallback_uuid
        if not uuid_action:
            continue

        raw_host = _safe_name(raw_media_server.get("host"))
        raw_mac = _safe_name(raw_media_server.get("MAC")) or _safe_name(
            raw_media_server.get("mac")
        )
        raw_type = _safe_name(raw_media_server.get("type"))

        parsed[uuid_action] = LoxoneMediaServer(
            uuid_action=uuid_action,
            name=_safe_name(raw_media_server.get("name")) or uuid_action,
            host=raw_host or None,
            mac=_normalize_mac(raw_mac) or raw_mac or None,
            states=_coerce_state_map(raw_media_server.get("states")),
            server_type=int(raw_type) if raw_type in {"0", "1", "2"} else None,
        )

    return parsed


def _normalize_mac(value: str) -> str | None:
    compact = value.strip()
    if not compact:
        return None
    if _MAC_PLAIN_RE.fullmatch(compact):
        return compact.upper()
    if _MAC_SEPARATED_RE.fullmatch(compact):
        return compact.replace("-", "").replace(":", "").upper()
    return None
