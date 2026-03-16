"""Helpers for local Intercom MJPEG proxy URLs and runtime targets."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant

from .const import DOMAIN

INTERCOM_STREAM_PROXY_URL = f"/api/{DOMAIN}/intercom_stream/{{serial}}/{{uuid_action}}"
_RUNTIME_TARGETS_ATTR = "_intercom_stream_proxy_targets"


def intercom_stream_proxy_path(serial: str, uuid_action: str) -> str:
    return (
        f"/api/{DOMAIN}/intercom_stream/"
        f"{quote(str(serial).strip(), safe='')}/"
        f"{quote(str(uuid_action).strip(), safe='')}"
    )


def intercom_stream_proxy_url(bridge, uuid_action: str) -> str | None:
    base_url = hass_base_url(getattr(bridge, "hass", None))
    if base_url is None:
        return None
    return f"{base_url}{intercom_stream_proxy_path(str(getattr(bridge, 'serial', '')), uuid_action)}"


def set_intercom_stream_target(
    bridge,
    uuid_action: str,
    *,
    target_url: str,
    username: str | None,
    password: str,
) -> None:
    targets = getattr(bridge, _RUNTIME_TARGETS_ATTR, None)
    if not isinstance(targets, dict):
        targets = {}
        setattr(bridge, _RUNTIME_TARGETS_ATTR, targets)
    targets[str(uuid_action)] = {
        "target_url": str(target_url),
        "username": username,
        "password": password,
    }


def intercom_stream_target(bridge, uuid_action: str) -> dict[str, Any] | None:
    targets = getattr(bridge, _RUNTIME_TARGETS_ATTR, None)
    if not isinstance(targets, dict):
        return None
    raw = targets.get(str(uuid_action))
    if not isinstance(raw, dict):
        return None
    target_url = raw.get("target_url")
    if not isinstance(target_url, str) or not target_url.strip():
        return None
    username = raw.get("username")
    if not isinstance(username, str) or not username.strip():
        username = None
    password = raw.get("password")
    if not isinstance(password, str):
        password = ""
    return {
        "target_url": target_url.strip(),
        "username": username,
        "password": password,
    }


def clear_intercom_stream_targets(bridge) -> None:
    setattr(bridge, _RUNTIME_TARGETS_ATTR, {})


def hass_base_url(hass: HomeAssistant | None) -> str | None:
    if hass is None:
        return None

    config = getattr(hass, "config", None)
    if config is not None:
        for attr in ("internal_url", "external_url"):
            value = getattr(config, attr, None)
            if isinstance(value, str):
                cleaned = value.strip().rstrip("/")
                if cleaned:
                    return cleaned

        api = getattr(config, "api", None)
        if api is not None:
            port = getattr(api, "port", None)
            host = getattr(api, "local_ip", None) or "127.0.0.1"
            if isinstance(port, int) and port > 0:
                scheme = "https" if bool(getattr(api, "use_ssl", False)) else "http"
                return f"{scheme}://{host}:{port}"

    return None
