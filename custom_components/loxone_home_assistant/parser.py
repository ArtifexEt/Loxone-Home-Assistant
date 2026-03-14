"""Parser for Loxone `LoxAPP3.json` files."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from .models import LoxoneControl, LoxoneMediaServer, LoxoneStateRef, LoxoneStructure
from .server_model import detect_server_model_from_mapping

_MAC_PLAIN_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
_MAC_SEPARATED_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def parse_structure(payload: Mapping[str, Any]) -> LoxoneStructure:
    """Parse the Loxone structure into flat control and state maps."""
    rooms = {
        room_uuid: _safe_name(room.get("name")) for room_uuid, room in payload.get("rooms", {}).items()
    }
    room_images = {
        room_uuid: _coerce_icon_path(room.get("image"))
        for room_uuid, room in payload.get("rooms", {}).items()
    }
    categories = {
        category_uuid: _safe_name(category.get("name"))
        for category_uuid, category in payload.get("cats", {}).items()
    }
    category_images = {
        category_uuid: _coerce_icon_path(category.get("image"))
        for category_uuid, category in payload.get("cats", {}).items()
    }
    ms_info_raw = payload.get("msInfo")
    ms_info = ms_info_raw if isinstance(ms_info_raw, Mapping) else {}
    server_model = detect_server_model_from_mapping(ms_info)
    default_hub_name = f"Loxone {server_model}"

    controls: list[LoxoneControl] = []
    controls_by_action: dict[str, LoxoneControl] = {}

    def visit(
        control_uuid: str,
        raw: Mapping[str, Any],
        parent: LoxoneControl | None = None,
        inherited_room: str | None = None,
        inherited_category: str | None = None,
        inherited_path: tuple[str, ...] = (),
    ) -> None:
        name = _safe_name(raw.get("name")) or control_uuid
        path = inherited_path + (name,)
        room_uuid = raw.get("room") or inherited_room
        category_uuid = raw.get("cat") or inherited_category
        uuid_action = raw.get("uuidAction") or control_uuid
        icon = _coerce_icon_path(raw.get("defaultIcon"))
        if icon is None:
            icon = _coerce_icon_path(raw.get("image"))
        if icon is None and category_uuid:
            icon = category_images.get(category_uuid)
        if icon is None and room_uuid:
            icon = room_images.get(room_uuid)
        control = LoxoneControl(
            uuid=control_uuid,
            uuid_action=uuid_action,
            name=name,
            type=_safe_name(raw.get("type")) or "Unknown",
            room_uuid=room_uuid,
            room_name=rooms.get(room_uuid),
            category_uuid=category_uuid,
            category_name=categories.get(category_uuid),
            states=_coerce_control_state_map(raw),
            details=_coerce_details(raw),
            parent_uuid_action=parent.uuid_action if parent else None,
            path=path,
            is_secured=bool(raw.get("isSecured", False)),
            icon=icon,
        )
        controls.append(control)
        controls_by_action[control.uuid_action] = control

        sub_controls = raw.get("subControls", {})
        if isinstance(sub_controls, Mapping):
            for sub_uuid, sub_raw in sub_controls.items():
                if isinstance(sub_raw, Mapping):
                    visit(
                        sub_uuid,
                        sub_raw,
                        parent=control,
                        inherited_room=room_uuid,
                        inherited_category=category_uuid,
                        inherited_path=path,
                    )

    for control_uuid, raw_control in payload.get("controls", {}).items():
        if isinstance(raw_control, Mapping):
            visit(control_uuid, raw_control)

    _infer_parent_links_from_uuid_action(controls, controls_by_action)
    _infer_parent_links_from_details(controls, controls_by_action)
    _rebuild_paths_from_parent_links(controls_by_action)
    states = _build_state_refs(controls)
    media_servers_by_uuid_action = _parse_media_servers(payload.get("mediaServer"))

    return LoxoneStructure(
        miniserver_name=_safe_name(ms_info.get("msName")) or default_hub_name,
        server_model=server_model,
        serial=_safe_name(ms_info.get("serialNr")) or "unknown",
        loxapp_version=_safe_name(payload.get("lastModified")) or "",
        controls=controls,
        controls_by_action=controls_by_action,
        states=states,
        media_servers_by_uuid_action=media_servers_by_uuid_action,
    )


def _coerce_state_map(raw_states: Mapping[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for state_name, state_uuid in raw_states.items():
        if isinstance(state_uuid, str):
            states[state_name] = _normalize_uuid(state_uuid)
    return states


def _coerce_control_state_map(raw_control: Mapping[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}

    raw_states = raw_control.get("states")
    if isinstance(raw_states, Mapping):
        states.update(_coerce_state_map(raw_states))

    # Some controls (for example CentralAudioZone variants) publish live UUIDs in
    # `events` instead of `states`. Keep those UUIDs subscribed as regular states.
    raw_events = raw_control.get("events")
    if isinstance(raw_events, Mapping):
        for event_name, event_uuid in _coerce_state_map(raw_events).items():
            states.setdefault(event_name, event_uuid)

    return states


def _coerce_details(raw_control: Mapping[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}

    raw_details = raw_control.get("details")
    if isinstance(raw_details, Mapping):
        details.update(raw_details)

    raw_secured_details = raw_control.get("securedDetails")
    if isinstance(raw_secured_details, Mapping):
        details["securedDetails"] = dict(raw_secured_details)

    return details


def _safe_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_icon_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_uuid(value: str) -> str:
    compact = value.strip().lower().replace("-", "")
    if len(compact) != 32:
        return value.strip().lower()
    try:
        return str(uuid.UUID(hex=compact))
    except ValueError:
        return value.strip().lower()


def _infer_parent_links_from_uuid_action(
    controls: list[LoxoneControl],
    controls_by_action: dict[str, LoxoneControl],
) -> None:
    """Infer parent links for flat exports where sub-controls use `parent/child` uuidAction."""
    for control in controls:
        if control.parent_uuid_action or "/" not in control.uuid_action:
            continue
        parent_uuid_action = control.uuid_action.rsplit("/", 1)[0]
        if parent_uuid_action in controls_by_action:
            control.parent_uuid_action = parent_uuid_action


def _infer_parent_links_from_details(
    controls: list[LoxoneControl],
    controls_by_action: dict[str, LoxoneControl],
) -> None:
    """Infer parent links using uuidAction references embedded in control details."""
    action_lookup = {
        uuid_action.casefold(): uuid_action for uuid_action in controls_by_action
    }

    for parent_control in controls:
        if not parent_control.details:
            continue

        for child_uuid_action in _extract_detail_action_references(
            parent_control.details, action_lookup
        ):
            if child_uuid_action == parent_control.uuid_action:
                continue
            child_control = controls_by_action.get(child_uuid_action)
            if child_control is None or child_control.parent_uuid_action:
                continue
            child_control.parent_uuid_action = parent_control.uuid_action


def _extract_detail_action_references(
    raw: Any,
    action_lookup: Mapping[str, str],
) -> set[str]:
    matches: set[str] = set()
    stack: list[Any] = [raw]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        if not isinstance(current, str):
            continue

        value = current.strip()
        if not value:
            continue

        direct_match = action_lookup.get(value.casefold())
        if direct_match is not None:
            matches.add(direct_match)
            continue

        lowered = value.casefold()
        for prefix in ("jdev/sps/io/", "dev/sps/io/"):
            if lowered.startswith(prefix):
                candidate = value[len(prefix) :].split("/", 1)[0].strip()
                prefixed_match = action_lookup.get(candidate.casefold())
                if prefixed_match is not None:
                    matches.add(prefixed_match)
                break

    return matches


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
    if not isinstance(raw_media_servers, Mapping):
        return parsed

    for media_server_uuid, raw_media_server in raw_media_servers.items():
        if not isinstance(raw_media_server, Mapping):
            continue

        fallback_uuid = _safe_name(media_server_uuid)
        uuid_action = _safe_name(raw_media_server.get("uuidAction")) or fallback_uuid
        if not uuid_action:
            continue

        raw_host = _safe_name(raw_media_server.get("host"))
        raw_mac = _safe_name(raw_media_server.get("mac"))
        normalized_mac = _normalize_mac(raw_mac)
        raw_states = raw_media_server.get("states")

        parsed[uuid_action] = LoxoneMediaServer(
            uuid_action=uuid_action,
            name=_safe_name(raw_media_server.get("name")) or uuid_action,
            host=raw_host or None,
            mac=normalized_mac or (raw_mac or None),
            states=_coerce_state_map(raw_states) if isinstance(raw_states, Mapping) else {},
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
