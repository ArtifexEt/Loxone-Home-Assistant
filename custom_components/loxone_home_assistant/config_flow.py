"""Config flow for the Loxone integration."""

from __future__ import annotations

import logging
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
from .connection_config import merge_connection_config
from .const import (
    CONF_CLIENT_UUID,
    CONF_ENABLE_LIGHT_MOOD_SELECT,
    CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    CONF_INTERCOM_LANGUAGE,
    CONF_INTERCOM_PASSWORD,
    CONF_INTERCOM_USERNAME,
    CONF_LOXAPP_VERSION,
    CONF_SERIAL,
    CONF_SERVER_MODEL,
    CONF_SOFTWARE_VERSION,
    CONF_TOKEN,
    CONF_TOKEN_VALID_UNTIL,
    CONF_USE_LOXONE_ICONS,
    CONF_USE_TLS,
    DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    DEFAULT_PORT,
    DEFAULT_SCAN_TIMEOUT,
    DEFAULT_USE_LOXONE_ICONS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    INTEGRATION_TITLE,
    INTERCOM_LANGUAGES,
)
from .options import option_enabled
from .versioning import MIN_SUPPORTED_VERSION_TEXT

_LOGGER = logging.getLogger(__name__)
_ENTITY_OPTION_DEFAULTS = {
    CONF_ENABLE_LIGHT_MOOD_SELECT: DEFAULT_ENABLE_LIGHT_MOOD_SELECT,
    CONF_EXPOSE_CONTROLLER_CHILD_LIGHTS: DEFAULT_EXPOSE_CONTROLLER_CHILD_LIGHTS,
    CONF_USE_LOXONE_ICONS: DEFAULT_USE_LOXONE_ICONS,
}


class LoxoneCommunityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Loxone."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth_data: dict[str, Any] = {}
        self._devices: list[DiscoveryResult] = []
        self._pending_entry_data: dict[str, Any] | None = None
        self._pending_entry_title: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["auto", "manual"],
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self._async_show_setup_form()
        if self._pending_entry_data is None or self._pending_entry_title is None:
            return self._async_show_auto_form(errors={})

        title, self._pending_entry_title = self._pending_entry_title, None
        data, self._pending_entry_data = self._pending_entry_data, None
        return self.async_create_entry(
            title=title,
            data={
                **data,
                **{
                    key: option_enabled(user_input.get(key), default)
                    for key, default in _ENTITY_OPTION_DEFAULTS.items()
                },
            },
        )

    async def async_step_auto(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_entry_data = None
            self._pending_entry_title = None
            self._auth_data = {
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            }
            _LOGGER.debug("Loxone flow: starting discovery")

            self._devices = await async_discover_miniservers(
                self.hass, timeout=DEFAULT_SCAN_TIMEOUT
            )
            _LOGGER.debug("Loxone flow: discovery found %s devices", len(self._devices))

            if len(self._devices) == 1:
                return await self._async_finish_setup(self._devices[0], from_manual=False)
            if len(self._devices) > 1:
                return await self.async_step_select()

            errors["base"] = "no_device_found"
            return await self.async_step_manual(errors=errors)

        return self._async_show_auto_form(errors=errors)

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
            self._pending_entry_data = None
            self._pending_entry_title = None
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

        return self._async_show_manual_form(errors=errors)

    def _async_show_setup_form(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="finish",
            data_schema=vol.Schema(
                {
                    vol.Required(key, default=default): bool
                    for key, default in _ENTITY_OPTION_DEFAULTS.items()
                }
            ),
        )

    def _async_show_auto_form(self, *, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="auto",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    def _async_show_manual_form(
        self,
        *,
        errors: dict[str, str],
        host: str = "",
        port: int = DEFAULT_PORT,
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host): str,
                    vol.Required(CONF_PORT, default=port): int,
                    vol.Required(CONF_USERNAME, default=self._auth_data.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_VERIFY_SSL,
                        default=self._auth_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): bool,
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
        except (LoxoneUnsupportedError, LoxoneAuthenticationError, LoxoneConnectionError) as err:
            error = _connection_error_key(err)
            if error == "invalid_auth" and not from_manual:
                return self._async_show_auto_form(errors={"base": error})
            return self._async_show_manual_form(
                errors={"base": error}, host=device.host, port=device.port
            )

        await self.async_set_unique_id(info[CONF_SERIAL])
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: device.host,
                CONF_PORT: device.port,
                CONF_USE_TLS: info[CONF_USE_TLS],
            }
        )

        self._pending_entry_title = _normalize_entry_title(info["title"])
        self._pending_entry_data = {
            **entry_data,
            CONF_CLIENT_UUID: info[CONF_CLIENT_UUID],
            CONF_SERIAL: info[CONF_SERIAL],
            CONF_LOXAPP_VERSION: info[CONF_LOXAPP_VERSION],
            CONF_SOFTWARE_VERSION: info[CONF_SOFTWARE_VERSION],
            CONF_SERVER_MODEL: info[CONF_SERVER_MODEL],
            CONF_USE_TLS: info[CONF_USE_TLS],
            CONF_TOKEN: info.get(CONF_TOKEN),
            CONF_TOKEN_VALID_UNTIL: info.get(CONF_TOKEN_VALID_UNTIL),
        }
        return self._async_show_setup_form()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return LoxoneCommunityOptionsFlow()


class LoxoneCommunityOptionsFlow(OptionsFlow):
    """Options flow for Loxone."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        config_entry = self.config_entry
        entry_options = config_entry.options
        stored = merge_connection_config(config_entry.data, entry_options)

        if user_input is not None:
            options = dict(user_input)
            # Blank fields keep password overrides from earlier option edits.
            for key in (CONF_PASSWORD, CONF_INTERCOM_PASSWORD):
                if options.get(key):
                    continue
                if key in entry_options:
                    options[key] = entry_options[key]
                else:
                    options.pop(key, None)
            merged = merge_connection_config(stored, options)
            if any(merged.get(key) != stored.get(key) for key in (CONF_HOST, CONF_PORT)):
                # Detect the transport again when the connection target changes.
                merged[CONF_USE_TLS] = True
            try:
                info = await _async_validate_input(self.hass, merged)
            except (LoxoneUnsupportedError, LoxoneAuthenticationError, LoxoneConnectionError) as err:
                errors["base"] = _connection_error_key(err)
            else:
                options[CONF_USE_TLS] = info[CONF_USE_TLS]
                return self.async_create_entry(title="", data=options)

        form_data = {**stored, **(user_input or {})}
        host_default = _coerce_text(form_data.get(CONF_HOST), "")
        port_default = _coerce_int(form_data.get(CONF_PORT), DEFAULT_PORT)
        username_default = _coerce_text(form_data.get(CONF_USERNAME), "")
        intercom_username_default = _coerce_text(form_data.get(CONF_INTERCOM_USERNAME), "")
        language = _coerce_text(form_data.get(CONF_INTERCOM_LANGUAGE), "auto")
        language_default = language if language in INTERCOM_LANGUAGES else "auto"
        verify_ssl_default = option_enabled(form_data.get(CONF_VERIFY_SSL), DEFAULT_VERIFY_SSL)
        serial = str(form_data.get(CONF_SERIAL) or "unknown")
        loxapp_version = str(form_data.get(CONF_LOXAPP_VERSION) or "unknown")
        active_bridge = getattr(config_entry, "runtime_data", None)
        active_version = getattr(active_bridge, "software_version", None)
        current_api_version = _coerce_text(
            active_version or form_data.get(CONF_SOFTWARE_VERSION), "unknown"
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host_default): str,
                    vol.Required(CONF_PORT, default=port_default): int,
                    vol.Required(CONF_USERNAME, default=username_default): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Optional(
                        CONF_INTERCOM_USERNAME,
                        default=intercom_username_default,
                    ): str,
                    vol.Optional(CONF_INTERCOM_PASSWORD, default=""): str,
                    vol.Optional(
                        CONF_INTERCOM_LANGUAGE, default=language_default
                    ): vol.In(INTERCOM_LANGUAGES),
                    vol.Required(CONF_VERIFY_SSL, default=verify_ssl_default): bool,
                    **{
                        vol.Required(
                            key, default=option_enabled(form_data.get(key), default)
                        ): bool
                        for key, default in _ENTITY_OPTION_DEFAULTS.items()
                    },
                }
            ),
            description_placeholders={
                "entry_title": config_entry.title or INTEGRATION_TITLE,
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
        await bridge.async_initialize(probe_legacy=True)
        _LOGGER.debug("Loxone flow: bridge initialize done")
        return {
            "title": bridge.miniserver_name,
            CONF_SERIAL: bridge.serial,
            CONF_CLIENT_UUID: bridge.client_uuid,
            CONF_LOXAPP_VERSION: bridge.loxapp_version,
            CONF_SOFTWARE_VERSION: bridge.software_version,
            CONF_SERVER_MODEL: bridge.server_model,
            CONF_USE_TLS: bridge.use_tls,
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


def _connection_error_key(error: Exception) -> str:
    """Select the form error for a failed connection attempt."""
    if isinstance(error, LoxoneVersionUnsupportedError):
        return "unsupported_version"
    if isinstance(error, LoxoneAuthenticationError):
        return "invalid_auth"
    if isinstance(error, LoxoneUnsupportedError):
        return "legacy_not_supported"
    return "cannot_connect"
