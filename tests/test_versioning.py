"""Tests for Miniserver version compatibility helpers."""

from __future__ import annotations

import unittest

from tests._loader import load_integration_module

versioning = load_integration_module("custom_components.loxone_home_assistant.versioning")
is_supported_miniserver_version = versioning.is_supported_miniserver_version
parse_miniserver_version = versioning.parse_miniserver_version


class VersioningTests(unittest.TestCase):
    """Validate Loxone software-version parsing and checks."""

    def test_parse_miniserver_version_returns_components(self) -> None:
        self.assertEqual(parse_miniserver_version("14.7.6.19"), (14, 7, 6, 19))
        self.assertEqual(parse_miniserver_version("10.2"), (10, 2))

    def test_parse_miniserver_version_rejects_invalid_values(self) -> None:
        self.assertIsNone(parse_miniserver_version(None))
        self.assertIsNone(parse_miniserver_version("unknown"))

    def test_supported_miniserver_version_requires_at_least_10_2(self) -> None:
        self.assertFalse(is_supported_miniserver_version("10.1.12.4"))
        self.assertTrue(is_supported_miniserver_version("10.2.0.0"))
        self.assertTrue(is_supported_miniserver_version("14.7.6.19"))


if __name__ == "__main__":
    unittest.main()
