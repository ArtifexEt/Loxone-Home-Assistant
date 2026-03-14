"""Tests for parsing Loxone structures."""

from __future__ import annotations

import unittest

from tests._loader import load_integration_module

load_integration_module("custom_components.loxone_home_assistant.models")
parse_structure = load_integration_module(
    "custom_components.loxone_home_assistant.parser"
).parse_structure


class ParseStructureTests(unittest.TestCase):
    """Verify `LoxAPP3.json` parsing without real credentials."""

    def test_parse_structure_flattens_controls_and_subcontrols(self) -> None:
        payload = {
            "msInfo": {
                "msName": "Dom",
                "serialNr": "1234567890",
            },
            "lastModified": "2026-03-13T20:00:00",
            "rooms": {
                "room-1": {"name": "Salon"},
            },
            "cats": {
                "cat-1": {"name": "Swiatla"},
            },
            "controls": {
                "main-light": {
                    "name": "Lampy",
                    "type": "LightControllerV2",
                    "uuidAction": "action-main-light",
                    "room": "room-1",
                    "cat": "cat-1",
                    "states": {
                        "activeMoods": "state-active-moods",
                    },
                    "subControls": {
                        "sub-dimmer": {
                            "name": "Sufit",
                            "type": "Dimmer",
                            "uuidAction": "action-sub-dimmer",
                            "states": {
                                "position": "state-position",
                            },
                        }
                    },
                }
            },
        }

        structure = parse_structure(payload)

        self.assertEqual(structure.miniserver_name, "Dom")
        self.assertEqual(structure.server_model, "Miniserver")
        self.assertEqual(structure.serial, "1234567890")
        self.assertEqual(structure.loxapp_version, "2026-03-13T20:00:00")
        self.assertEqual(len(structure.controls), 2)

        main_control = structure.controls_by_action["action-main-light"]
        sub_control = structure.controls_by_action["action-sub-dimmer"]

        self.assertEqual(main_control.display_name, "Lampy")
        self.assertEqual(main_control.room_name, "Salon")
        self.assertEqual(main_control.category_name, "Swiatla")

        self.assertEqual(sub_control.display_name, "Lampy Sufit")
        self.assertEqual(sub_control.room_name, "Salon")
        self.assertEqual(sub_control.category_name, "Swiatla")
        self.assertEqual(sub_control.parent_uuid_action, "action-main-light")

        state_ref = structure.states["state-position"]
        self.assertEqual(state_ref.control_uuid_action, "action-sub-dimmer")
        self.assertEqual(state_ref.control_name, "Lampy Sufit")
        self.assertEqual(state_ref.state_name, "position")

    def test_parse_structure_infers_flat_parent_links_from_details_references(self) -> None:
        payload = {
            "msInfo": {
                "msName": "Dom",
                "serialNr": "1234567890",
            },
            "controls": {
                "central": {
                    "name": "Central",
                    "type": "Switch",
                    "uuidAction": "action-central",
                    "details": {
                        "commands": [
                            "jdev/sps/io/action-sub-by-detail/on",
                        ]
                    },
                },
                "flat-sub-via-slash": {
                    "name": "Strefa 1",
                    "type": "Switch",
                    "uuidAction": "action-central/1",
                    "states": {
                        "active": "state-slash",
                    },
                },
                "flat-sub-via-details": {
                    "name": "Strefa 2",
                    "type": "Switch",
                    "uuidAction": "action-sub-by-detail",
                    "states": {
                        "active": "state-details",
                    },
                },
            },
        }

        structure = parse_structure(payload)

        sub_via_slash = structure.controls_by_action["action-central/1"]
        sub_via_details = structure.controls_by_action["action-sub-by-detail"]

        self.assertEqual(sub_via_slash.parent_uuid_action, "action-central")
        self.assertEqual(sub_via_slash.display_name, "Central Strefa 1")
        self.assertEqual(sub_via_details.parent_uuid_action, "action-central")
        self.assertEqual(sub_via_details.display_name, "Central Strefa 2")

        state_ref = structure.states["state-details"]
        self.assertEqual(state_ref.control_name, "Central Strefa 2")

    def test_parse_structure_detects_go_and_uses_model_based_default_name(self) -> None:
        payload = {
            "msInfo": {
                "deviceType": "Go",
                "serialNr": "1234567890",
            },
            "controls": {},
        }

        structure = parse_structure(payload)

        self.assertEqual(structure.server_model, "Miniserver Go")
        self.assertEqual(structure.miniserver_name, "Loxone Miniserver Go")

    def test_parse_structure_keeps_secured_details(self) -> None:
        payload = {
            "msInfo": {
                "msName": "Dom",
                "serialNr": "1234567890",
            },
            "controls": {
                "intercom-1": {
                    "name": "Intercom Front",
                    "type": "DoorController",
                    "uuidAction": "action-intercom",
                    "details": {
                        "deviceType": 1,
                    },
                    "securedDetails": {
                        "videoInfo": {
                            "streamUrl": "/dev/secured-stream.mjpg",
                        }
                    },
                }
            },
        }

        structure = parse_structure(payload)
        control = structure.controls_by_action["action-intercom"]

        self.assertEqual(control.details["deviceType"], 1)
        self.assertEqual(
            control.details["securedDetails"]["videoInfo"]["streamUrl"],
            "/dev/secured-stream.mjpg",
        )

    def test_parse_structure_merges_event_uuids_into_runtime_state_map(self) -> None:
        payload = {
            "msInfo": {
                "msName": "Dom",
                "serialNr": "1234567890",
            },
            "controls": {
                "central-audio": {
                    "name": "Audio Central",
                    "type": "CentralAudioZone",
                    "uuidAction": "action-central-audio",
                    "states": {
                        "playState": "state-play",
                        "power": "state-power",
                    },
                    "events": {
                        "sourceList": "event-source-list",
                        "playState": "event-should-not-override-state",
                    },
                }
            },
        }

        structure = parse_structure(payload)
        control = structure.controls_by_action["action-central-audio"]

        self.assertEqual(control.states["playState"], "state-play")
        self.assertEqual(control.states["power"], "state-power")
        self.assertEqual(control.states["sourceList"], "event-source-list")
        self.assertIn("event-source-list", structure.states)
        self.assertEqual(structure.states["event-source-list"].state_name, "sourceList")


if __name__ == "__main__":
    unittest.main()
