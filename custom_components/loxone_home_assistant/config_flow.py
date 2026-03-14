"""Config flow for the Loxone integration."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback

from .bridge import (
    DiscoveryResult,
    LoxoneAuthenticationError,
    LoxoneBridge,
    LoxoneConnectionError,
    LoxoneUnsupportedError,
    LoxoneVersionUnsupportedError,
    async_discover_miniservers,
)
from .const import (
    CONF_CLIENT_UUID,
    CONF_ENABLE_LIGHT_MOOD_SELECT,
    CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    CONF_LOXAPP_VERSION,
    CONF_SCAN_TIMEOUT,
    CONF_SERVER_MODEL,
    CONF_SERIAL,
    CONF_SOFTWARE_VERSION,
    CONF_TOKEN,
    CONF_TOKEN_VALID_UNTIL,
    CONF_USE_TLS,
    DEFAULT_PORT,
    DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DEFAULT_SCAN_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    INTEGRATION_TITLE,
)
from .versioning import MIN_SUPPORTED_VERSION_TEXT

_LOGGER = logging.getLogger(__name__)


class LoxoneCommunityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Loxone."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth_data: dict[str, Any] = {}
        self._devices: list[DiscoveryResult] = []
        self._legacy_found = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._auth_data = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            }
            _LOGGER.warning("Loxone flow: starting discovery")

            discovery = await async_discover_miniservers(
                self.hass, timeout=DEFAULT_SCAN_TIMEOUT
            )
            self._devices = discovery.devices
            self._legacy_found = discovery.legacy_found
            _LOGGER.warning(
                "Loxone flow: discovery completed devices=%s legacy_found=%s",
                len(self._devices),
                self._legacy_found,
            )

            if len(self._devices) == 1:
                return await self._async_finish_setup(self._devices[0], from_manual=False)
            if len(self._devices) > 1:
                return await self.async_step_select()

            errors["base"] = "legacy_not_supported" if self._legacy_found else "no_device_found"
            return await self.async_step_manual(errors=errors)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            index = int(user_input["device"])
            return await self._async_finish_setup(self._devices[index], from_manual=False)

        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {str(index): device.label for index, device in enumerate(self._devices)}
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        errors = errors or {}

        if user_input is not None:
            self._auth_data = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
            }
            device = DiscoveryResult(
                host=user_input[CONF_HOST],
                port=int(user_input[CONF_PORT]),
                use_tls=True,
                label=user_input[CONF_HOST],
            )
            return await self._async_finish_setup(device, from_manual=True)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                }
            ),
            errors=errors,
        )

    async def _async_finish_setup(
        self, device: DiscoveryResult, *, from_manual: bool
    ) -> ConfigFlowResult:
        _LOGGER.debug("Loxone flow: validating selected device")
        entry_data = {
            **self._auth_data,
            CONF_HOST: device.host,
            CONF_PORT: device.port,
            CONF_USE_TLS: device.use_tls,
            CONF_SERVER_MODEL: device.server_model,
        }

        try:
            info = await _async_validate_input(self.hass, entry_data)
        except LoxoneVersionUnsupportedError:
            return self.async_show_form(
                step_id="manual",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=device.host): str,
                        vol.Required(CONF_PORT, default=device.port): int,
                        vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_VERIFY_SSL, default=self._auth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                    }
                ),
                errors={"base": "unsupported_version"},
            )
        except LoxoneAuthenticationError:
            if from_manual:
                return self.async_show_form(
                    step_id="manual",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_HOST, default=device.host): str,
                            vol.Required(CONF_PORT, default=device.port): int,
                            vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Required(CONF_VERIFY_SSL, default=self._auth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                        }
                    ),
                    errors={"base": "invalid_auth"},
                )
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                        vol.Required(CONF_PASSWORD): str,
                    }
                ),
                errors={"base": "invalid_auth"},
            )
        except LoxoneUnsupportedError:
            return self.async_show_form(
                step_id="manual",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=device.host): str,
                        vol.Required(CONF_PORT, default=device.port): int,
                        vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_VERIFY_SSL, default=self._auth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                    }
                ),
                errors={"base": "legacy_not_supported"},
            )
        except LoxoneConnectionError:
            return self.async_show_form(
                step_id="manual",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_HOST, default=device.host): str,
                        vol.Required(CONF_PORT, default=device.port): int,
                        vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                        vol.Required(CONF_PASSWORD): str,
                        vol.Required(CONF_VERIFY_SSL, default=self._auth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
                    }
                ),
                errors={"base": "cannot_connect"},
            )

        await self.async_set_unique_id(info[CONF_SERIAL])
        self._abort_if_unique_id_configured(updates={CONF_HOST: device.host, CONF_PORT: device.port})

        return self.async_create_entry(
            title=_normalize_entry_title(info["title"]),
            data={
                **entry_data,
                CONF_CLIENT_UUID: info[CONF_CLIENT_UUID],
                CONF_SERIAL: info[CONF_SERIAL],
                CONF_LOXAPP_VERSION: info[CONF_LOXAPP_VERSION],
                CONF_SOFTWARE_VERSION: info[CONF_SOFTWARE_VERSION],
                CONF_SERVER_MODEL: info[CONF_SERVER_MODEL],
                CONF_TOKEN: info.get(CONF_TOKEN),
                CONF_TOKEN_VALID_UNTIL: info.get(CONF_TOKEN_VALID_UNTIL),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return LoxoneCommunityOptionsFlow(config_entry)


class LoxoneCommunityOptionsFlow(OptionsFlow):
    """Options flow for Loxone."""

    def __init__(self, config_entry) -> None:
        self._config_entry_fallback = config_entry
        with contextlib.suppress(TypeError):
            super().__init__(config_entry)
            return
        with contextlib.suppress(AttributeError):
            # Older HA versions expose `config_entry` as a plain attribute.
            self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        config_entry = _resolve_options_config_entry(self)
        entry_data = _coerce_mapping(config_entry.data if config_entry else None)
        entry_options = _coerce_mapping(config_entry.options if config_entry else None)

        if user_input is not None:
            options = dict(user_input)
            # Keep currently stored password when options password is left empty.
            if not options.get(CONF_PASSWORD):
                options.pop(CONF_PASSWORD, None)
            merged = {**entry_data, **entry_options, **options}
            try:
                await _async_validate_input(self.hass, merged)
            except LoxoneVersionUnsupportedError:
                errors["base"] = "unsupported_version"
            except LoxoneAuthenticationError:
                errors["base"] = "invalid_auth"
            except LoxoneUnsupportedError:
                errors["base"] = "legacy_not_supported"
            except LoxoneConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="", data=options)

        data = {**entry_data, **entry_options}
        form_data = {**data, **(user_input or {})}
        host_default = _coerce_text(form_data.get(CONF_HOST), "")
        port_default = _coerce_int(form_data.get(CONF_PORT), DEFAULT_PORT)
        username_default = _coerce_text(form_data.get(CONF_USERNAME), "")
        verify_ssl_default = _coerce_bool(form_data.get(CONF_VERIFY_SSL), DEFAULT_VERIFY_SSL)
        scan_timeout_default = _coerce_int(
            form_data.get(CONF_SCAN_TIMEOUT), DEFAULT_SCAN_TIMEOUT
        )
        light_mood_select_default = _coerce_bool(
            form_data.get(CONF_ENABLE_LIGHT_MOOD_SELECT),
            DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
        )
        child_lights_default = _coerce_bool(
            form_data.get(CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS),
            DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
        )
        serial = str(form_data.get(CONF_SERIAL) or "unknown")
        loxapp_version = str(form_data.get(CONF_LOXAPP_VERSION) or "unknown")
        current_api_version = _coerce_text(form_data.get(CONF_SOFTWARE_VERSION), "unknown")
        if config_entry is not None:
            runtime_bridge = (
                self.hass.data.get(DOMAIN, {})
                .get("bridges", {})
                .get(config_entry.entry_id)
            )
            if runtime_bridge is not None:
                current_api_version = _coerce_text(
                    getattr(runtime_bridge, "software_version", None),
                    current_api_version,
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host_default): str,
                    vol.Required(CONF_PORT, default=port_default): int,
                    vol.Required(CONF_USERNAME, default=username_default): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Required(CONF_VERIFY_SSL, default=verify_ssl_default): bool,
                    vol.Required(CONF_SCAN_TIMEOUT, default=scan_timeout_default): int,
                    vol.Required(
                        CONF_ENABLE_LIGHT_MOOD_SELECT,
                        default=light_mood_select_default,
                    ): bool,
                    vol.Required(
                        CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
                        default=child_lights_default,
                    ): bool,
                }
            ),
            description_placeholders={
                "entry_title": (config_entry.title if config_entry else "") or INTEGRATION_TITLE,
                "serial": serial,
                "loxapp_version": loxapp_version,
                "current_api_version": current_api_version,
                "min_api_version": MIN_SUPPORTED_VERSION_TEXT,
            },
            errors=errors,
        )


async def _async_validate_input(hass, data: dict[str, Any]) -> dict[str, Any]:
    bridge = LoxoneBridge(hass, data)
    try:
        _LOGGER.debug("Loxone flow: bridge initialize start")
        await bridge.async_initialize()
        _LOGGER.debug("Loxone flow: bridge initialize done")
        return {
            "title": bridge.miniserver_name,
            CONF_SERIAL: bridge.serial,
            CONF_CLIENT_UUID: bridge.client_uuid,
            CONF_LOXAPP_VERSION: bridge.loxapp_version,
            CONF_SOFTWARE_VERSION: bridge.software_version,
            CONF_SERVER_MODEL: bridge.server_model,
            CONF_TOKEN: bridge.token,
            CONF_TOKEN_VALID_UNTIL: bridge.token_valid_until,
        }
    finally:
        await bridge.async_shutdown()


def _normalize_entry_title(raw_title: str) -> str:
    """Normalize generated config entry title for UI cards."""
    title = raw_title.strip()
    return title or INTEGRATION_TITLE


def _coerce_text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return default


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _resolve_options_config_entry(flow: LoxoneCommunityOptionsFlow):
    entry = getattr(flow, "config_entry", None)
    if entry is not None:
        return entry
    return getattr(flow, "_config_entry_fallback", None)
