"""Configuration shared by Miniserver setup and authentication."""

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .const import CONF_TOKEN, CONF_TOKEN_VALID_UNTIL

CONNECTION_DATA_KEYS = (CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)


def merge_connection_config(
    stored: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply settings without reusing a token for changed connection details."""
    merged = {**stored, **changes}
    if any(merged.get(key) != stored.get(key) for key in CONNECTION_DATA_KEYS):
        merged[CONF_TOKEN] = None
        merged[CONF_TOKEN_VALID_UNTIL] = None
    return merged
