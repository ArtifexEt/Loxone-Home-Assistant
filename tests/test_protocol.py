"""Tests for Loxone protocol helpers."""

from __future__ import annotations

import struct
import unittest
import uuid

from tests._loader import load_integration_module

protocol = load_integration_module("custom_components.loxone_home_assistant.protocol")
deserialize_value = protocol.deserialize_value
parse_api_key_payload = protocol.parse_api_key_payload
parse_header = protocol.parse_header
parse_text_state_table = protocol.parse_text_state_table
parse_value_state_table = protocol.parse_value_state_table


class ProtocolHelpersTests(unittest.TestCase):
    """Verify protocol parsing without talking to a real Miniserver."""

    def test_parse_header_extracts_identifier_and_estimated_flag(self) -> None:
        header = parse_header(bytes([0x03, 0x02, 0x01, 0, 0, 0, 0, 0]))
        self.assertEqual(header.identifier, 2)
        self.assertTrue(header.estimated)

    def test_parse_value_state_table_decodes_uuid_and_value(self) -> None:
        state_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        payload = state_uuid.bytes_le + struct.pack("<d", 42.5)

        decoded = parse_value_state_table(payload)

        self.assertEqual(decoded[str(state_uuid)], 42.5)

    def test_parse_text_state_table_decodes_padded_text(self) -> None:
        state_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        icon_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555")
        text = "Hello"
        encoded = text.encode("utf-8")
        padded = encoded + (b"\x00" * ((4 - len(encoded) % 4) % 4))
        payload = state_uuid.bytes_le + icon_uuid.bytes_le + struct.pack("<I", len(encoded)) + padded

        decoded = parse_text_state_table(payload)

        self.assertEqual(decoded[str(state_uuid)], text)

    def test_deserialize_value_parses_json_like_payloads(self) -> None:
        decoded = deserialize_value('{"httpsStatus": 1, "name": "Miniserver"}')

        self.assertEqual(decoded["httpsStatus"], 1)
        self.assertEqual(decoded["name"], "Miniserver")

    def test_deserialize_value_parses_loxone_literal_payloads(self) -> None:
        decoded = deserialize_value(
            "{'httpsStatus':1,'local':true,'isInTrust':false,'name':'Miniserver','note':null}"
        )

        self.assertEqual(decoded["httpsStatus"], 1)
        self.assertTrue(decoded["local"])
        self.assertFalse(decoded["isInTrust"])
        self.assertEqual(decoded["name"], "Miniserver")
        self.assertIsNone(decoded["note"])

    def test_parse_api_key_payload_reads_nested_value(self) -> None:
        payload = {
            "LL": {
                "value": (
                    "{'httpsStatus':1,'local':true,'isInTrust':false,"
                    "'name':'Miniserver','note':null}"
                ),
            }
        }

        decoded = parse_api_key_payload(payload)

        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["httpsStatus"], 1)
        self.assertTrue(decoded["local"])
        self.assertFalse(decoded["isInTrust"])
        self.assertEqual(decoded["name"], "Miniserver")
        self.assertIsNone(decoded["note"])

    def test_parse_api_key_payload_ignores_none_payload(self) -> None:
        self.assertIsNone(parse_api_key_payload(None))


if __name__ == "__main__":
    unittest.main()
