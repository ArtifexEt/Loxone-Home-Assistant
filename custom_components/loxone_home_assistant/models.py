"""Models for parsed Loxone structures."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any


if sys.version_info >= (3, 10):
    def slotted_dataclass(cls):
        return dataclass(slots=True)(cls)
else:
    def slotted_dataclass(cls):
        return dataclass(cls)


@slotted_dataclass
class LoxoneControl:
    """Representation of one Loxone control or sub-control."""

    uuid: str
    uuid_action: str
    name: str
    type: str
    room_uuid: str | None = None
    room_name: str | None = None
    category_uuid: str | None = None
    category_name: str | None = None
    states: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    parent_uuid_action: str | None = None
    path: tuple[str, ...] = field(default_factory=tuple)
    is_secured: bool = False

    @property
    def display_name(self) -> str:
        """Return a readable entity name."""
        if len(self.path) <= 1:
            return self.name
        return " ".join(self.path)

    def state_uuid(self, name: str) -> str | None:
        """Return the UUID for a named state."""
        return self.states.get(name)


@slotted_dataclass
class LoxoneStateRef:
    """Back-reference from a state UUID to its owning control."""

    control_uuid_action: str
    control_name: str
    control_type: str
    state_name: str


@slotted_dataclass
class LoxoneStructure:
    """Parsed Loxone structure."""

    miniserver_name: str
    server_model: str
    serial: str
    loxapp_version: str
    controls: list[LoxoneControl]
    controls_by_action: dict[str, LoxoneControl]
    states: dict[str, LoxoneStateRef]
