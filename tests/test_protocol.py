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

    def test_parse_header_extracts_identifier_flags_and_payload_length(self) -> None:
        header = parse_header(bytes([0x03, 0x02, 0x01, 0, 0x78, 0x56, 0x34, 0x12]))
        self.assertIsNotNone(header)
        self.assertEqual(header.identifier, 2)
        self.assertTrue(header.estimated)
        self.assertEqual(header.length, 0x12345678)

    def test_parse_header_ignores_non_header_frames(self) -> None:
        self.assertIsNone(parse_header(b"\x03\x02"))
        self.assertIsNone(parse_header(b"\xff\x02\x00\x00\x00\x00\x00\x00"))

    def test_parse_value_state_table_decodes_uuid_and_value(self) -> None:
        state_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        payload = state_uuid.bytes_le + struct.pack("<d", 42.5)

        decoded = parse_value_state_table(payload)

        self.assertEqual(decoded[str(state_uuid)], 42.5)

    def test_parse_text_state_table_uses_utf8_byte_lengths_and_skips_padding(self) -> None:
        state_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        other_uuid = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        icon_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555").bytes_le
        text = "Lubię"
        encoded = text.encode("utf-8")
        payload = (
            state_uuid.bytes_le + icon_uuid + struct.pack("<I", len(encoded)) + encoded + b"\x00\x00"
            + other_uuid.bytes_le + icon_uuid + struct.pack("<I", 0)
        )

        decoded = parse_text_state_table(payload)

        self.assertEqual(decoded, {str(state_uuid): text, str(other_uuid): ""})

    def test_parse_text_state_table_ignores_truncated_entries(self) -> None:
        state_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        truncated_uuid = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        icon = bytes(16)
        payload = (
            state_uuid.bytes_le + icon + struct.pack("<I", 4) + b"Good"
            + truncated_uuid.bytes_le + icon + struct.pack("<I", 12) + b"Bad"
        )

        self.assertEqual(parse_text_state_table(payload), {str(state_uuid): "Good"})

    def test_deserialize_value_parses_json_like_payloads(self) -> None:
        decoded = deserialize_value('{"httpsStatus": 1, "name": "Miniserver"}')

        self.assertEqual(decoded["httpsStatus"], 1)
        self.assertEqual(decoded["name"], "Miniserver")

    def test_deserialize_value_parses_loxone_literal_payloads(self) -> None:
        decoded = deserialize_value(
            r"""{'httpsStatus':1,'local':true,'isInTrust':false,'note':null,
                 'name':'O\'Brien true false null', "false":"Keep \"true\" here"}"""
        )

        self.assertEqual(decoded["httpsStatus"], 1)
        self.assertTrue(decoded["local"])
        self.assertFalse(decoded["isInTrust"])
        self.assertEqual(decoded["name"], "O'Brien true false null")
        self.assertEqual(decoded["false"], 'Keep "true" here')
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
