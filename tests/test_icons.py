"""Tests for icon path helpers."""

from __future__ import annotations

import unittest

from tests._loader import load_integration_module

icons = load_integration_module("custom_components.loxone_home_assistant.icons")


class IconHelpersTests(unittest.TestCase):
    """Validate normalization and proxy URL helpers."""

    def test_normalize_icon_path_accepts_relative_paths(self) -> None:
        self.assertEqual(
            icons.normalize_icon_path("IconsFilled/lightbulb-3.svg"),
            "IconsFilled/lightbulb-3.svg",
        )
        self.assertEqual(
            icons.normalize_icon_path("/IconsFilled\\lightbulb-3.svg"),
            "IconsFilled/lightbulb-3.svg",
        )

    def test_normalize_icon_path_rejects_absolute_or_unsafe_values(self) -> None:
        self.assertIsNone(icons.normalize_icon_path("https://mini.local/IconsFilled/a.svg"))
        self.assertIsNone(icons.normalize_icon_path("https://[::1/IconsFilled/a.svg"))
        self.assertIsNone(icons.normalize_icon_path("IconsFilled/a.svg?x=1"))
        self.assertIsNone(icons.normalize_icon_path("../IconsFilled/a.svg"))

    def test_icon_proxy_url_builds_encoded_local_url(self) -> None:
        self.assertEqual(
            icons.icon_proxy_url("1234567890", "IconsFilled/lightbulb-3.svg"),
            "/api/loxone_home_assistant/icon/1234567890/IconsFilled%2Flightbulb-3.svg",
        )

    def test_decode_icon_key_roundtrip(self) -> None:
        encoded = icons.encode_icon_key("IconsFilled/lightbulb-3.svg")
        self.assertEqual(encoded, "IconsFilled%2Flightbulb-3.svg")
        self.assertEqual(
            icons.decode_icon_key(encoded),
            "IconsFilled/lightbulb-3.svg",
        )

    def test_icon_content_type_accepts_images_and_headerless_svg(self) -> None:
        self.assertEqual(
            icons.icon_content_type("Image/SVG+XML; charset=utf-8", "icon.svg"),
            "image/svg+xml",
        )
        self.assertEqual(icons.icon_content_type("image/png", "icon.png"), "image/png")
        self.assertEqual(icons.icon_content_type(None, "icon.svg"), "image/svg+xml")

    def test_icon_content_type_rejects_non_image_responses(self) -> None:
        self.assertIsNone(icons.icon_content_type("text/html", "icon.svg"))
        self.assertIsNone(icons.icon_content_type("application/json", "icon.svg"))
        self.assertIsNone(icons.icon_content_type(None, "icon.png"))


if __name__ == "__main__":
    unittest.main()
