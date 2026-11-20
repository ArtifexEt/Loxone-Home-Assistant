"""Network, discovery and websocket bridge for Loxone."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import ssl
import sys
from time import monotonic
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, ClientSession, WSMessage, WSMsgType
from homeassistant.components.network import async_get_adapters
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .connection_config import CONNECTION_DATA_KEYS
from .const import (
    ACCESS_DENIED_STATE_CANDIDATES,
    ACCESS_GRANTED_STATE_CANDIDATES,
    ACCESS_TYPE_HINTS,
    APP_PERMISSION,
    CLIENT_INFO,
    CONF_CLIENT_UUID,
    CONF_INTERCOM_PASSWORD,
    CONF_INTERCOM_USERNAME,
    CONF_LOXAPP_VERSION,
    CONF_SCAN_TIMEOUT,
    CONF_SERVER_MODEL,
    CONF_SERIAL,
    CONF_SOFTWARE_VERSION,
    CONF_TOKEN,
    CONF_TOKEN_VALID_UNTIL,
    CONF_USE_LOXONE_ICONS,
    CONF_USE_TLS,
    DEFAULT_KEEPALIVE_SECONDS,
    DEFAULT_PORT,
    DEFAULT_SCAN_TIMEOUT,
    DEFAULT_USE_LOXONE_ICONS,
    DEFAULT_VERIFY_SSL,
    EVENT_ACCESS,
    EVENT_INTERCOM,
    WEB_PERMISSION,
)
from .icons import icon_proxy_url, normalize_icon_path
from .intercom import (
    intercom_call_state_name,
    intercom_doorbell_state_name,
    intercom_light_state_name,
    intercom_proximity_state_name,
    is_intercom_control,
)
from .legacy_crypto import LegacyCryptoError, LegacySession
from .models import LoxoneControl, LoxoneMediaServer, LoxoneStructure
from .parser import parse_structure
from .protocol import (
    PendingHeader,
    deserialize_value,
    normalize_uuid,
    parse_api_key_payload,
    parse_header,
    parse_text_state_table,
    parse_value_state_table,
)
from .server_model import DEFAULT_SERVER_MODEL, detect_server_model_from_mapping
from .versioning import (
    MIN_SUPPORTED_VERSION_TEXT,
    is_supported_miniserver_version,
    parse_miniserver_version,
)

_LOGGER = logging.getLogger(__name__)

try:  # Python 3.11+
    from datetime import UTC
except ImportError:  # Python <=3.10
    UTC = timezone.utc

if sys.version_info >= (3, 10):
    def slotted_dataclass(cls):
        return dataclass(slots=True)(cls)
else:
    def slotted_dataclass(cls):
        return dataclass(cls)

PROBE_PATH = "/jdev/cfg/apiKey"
LEGACY_PROBE_TIMEOUT = 2
LEGACY_CERTIFICATE_PATH = "/jdev/sys/getcertificate"
LEGACY_CERTIFICATE_TIMEOUT = 10
WS_PATH = "/ws/rfc6455"
LOXONE_EPOCH = datetime(2009, 1, 1, tzinfo=UTC)
KEEPALIVE_RESPONSE = 6
OUT_OF_SERVICE = 5
TEXT_MESSAGE = 0
FILE_MESSAGE = 1
VALUE_STATE_TABLE = 2
TEXT_STATE_TABLE = 3
CLOUD_DNS_HOST = "dns.loxonecloud.com"
REMOTE_CONNECT_HOST = "connect.loxonecloud.com"
REMOTE_CONNECT_TIMEOUT = 10
_REMOTE_RELAY_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.remcov3\.loxonecloud\.com"
)
_MAC_PLAIN_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
_MAC_SEPARATED_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_STATE_UPDATE_CONTROL_RE = re.compile(
    r"^dev/sps/(?:io|status)/(?P<uuid>[0-9A-Fa-f-]{32,36})(?:/.*)?$"
)
_VERSION_KEYS = (
    "softwareVersion",
    "version",
    "swVersion",
    "miniserverVersion",
)
ACCESS_TYPE_HINT_KEYS = tuple(hint.casefold() for hint in ACCESS_TYPE_HINTS)
ACCESS_EVENT_DETAIL_HINTS = (
    "code",
    "tag",
    "user",
    "auth",
    "history",
    "event",
    "last",
    "nfc",
    "keypad",
)
ACCESS_DENIED_EVENT_HINTS = (
    "denied",
    "wrong",
    "invalid",
    "error",
    "fail",
    "rejected",
)
ACCESS_GRANTED_EVENT_HINTS = (
    "granted",
    "access",
    "success",
    "valid",
    "ok",
    "accepted",
)
ACCESS_DENIED_VALUE_HINTS = (
    "accessdenied",
    "wrongcode",
    "invalidcode",
    "codeerror",
    "denied",
    "wrong",
    "invalid",
    "error",
    "fail",
    "rejected",
    "unauthorized",
    "notallowed",
    "incorrect",
    "badcode",
    "falsch",
    "niepopraw",
    "bled",
    "odrzucon",
)
ACCESS_GRANTED_VALUE_HINTS = (
    "accessgranted",
    "codesuccess",
    "granted",
    "success",
    "accepted",
    "validcode",
    "correctcode",
    "authorized",
    "allowed",
    "unlock",
    "opened",
    "otwart",
    "popraw",
)
SYSTEM_STATS_COMMANDS: dict[str, str] = {
    "numtasks": "dev/sys/numtasks",
    "cpu": "dev/sys/cpu",
    "heap": "dev/sys/heap",
    "ints": "dev/sys/ints",
}
SYSTEM_STATS_STATE_PREFIX = "sysdiag-"
SYSTEM_STATS_REFRESH_SECONDS = 30
SYSTEM_STATS_MIN_REFRESH_SECONDS = 5


class LoxoneError(Exception):
    """Base exception for bridge errors."""


class LoxoneConnectionError(LoxoneError):
    """Raised when the Miniserver cannot be reached."""


class _LoxoneWebSocketConnectionError(LoxoneConnectionError):
    """Raised when opening the websocket fails before authentication."""


class LoxoneAuthenticationError(LoxoneError):
    """Raised when Loxone rejects credentials or token auth."""


class LoxoneUnsupportedError(LoxoneError):
    """Raised for intentionally unsupported configurations."""


class LoxoneVersionUnsupportedError(LoxoneUnsupportedError):
    """Raised when Miniserver software is outside supported API range."""


@slotted_dataclass
class DiscoveryResult:
    """One discovered Miniserver candidate."""

    host: str
    port: int
    use_tls: bool
    label: str
    server_model: str = DEFAULT_SERVER_MODEL


@slotted_dataclass
class DiscoverySummary:
    """Discovery result set."""

    devices: list[DiscoveryResult]
    legacy_found: bool = False


class LoxoneBridge:
    """State bridge between Home Assistant and a Loxone Miniserver."""

    def __init__(self, hass: HomeAssistant, data: Mapping[str, Any]) -> None:
        self.hass = hass
        self._connection_data = {key: data[key] for key in CONNECTION_DATA_KEYS if key in data}
        raw_host = str(data[CONF_HOST]).strip()
        self.host, self._ws_path_prefix = _resolve_miniserver_target(raw_host)
        self._remote_serial = (
            _extract_mac_from_path(self._ws_path_prefix) if self.host == CLOUD_DNS_HOST else None
        )
        self.port: int = int(data.get(CONF_PORT, DEFAULT_PORT))
        self.username: str = data[CONF_USERNAME]
        self.password: str = data[CONF_PASSWORD]
        raw_intercom_username = data.get(CONF_INTERCOM_USERNAME)
        self.intercom_username: str | None = (
            str(raw_intercom_username).strip() if raw_intercom_username is not None else None
        ) or None
        raw_intercom_password = data.get(CONF_INTERCOM_PASSWORD)
        self.intercom_password: str | None = (
            str(raw_intercom_password) if raw_intercom_password is not None else None
        )
        if self.intercom_password == "":
            self.intercom_password = None
        remote = self._remote_serial is not None
        self.verify_ssl: bool = remote or bool(data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        self.use_tls: bool = remote or bool(data.get(CONF_USE_TLS, True))
        self.use_loxone_icons: bool = bool(
            data.get(CONF_USE_LOXONE_ICONS, DEFAULT_USE_LOXONE_ICONS)
        )

        self.serial: str = str(data.get(CONF_SERIAL, "unknown"))
        self.loxapp_version: str = str(data.get(CONF_LOXAPP_VERSION, ""))
        self.software_version: str | None = (
            str(data.get(CONF_SOFTWARE_VERSION))
            if data.get(CONF_SOFTWARE_VERSION) is not None
            else None
        )
        self.client_uuid: str = str(data.get(CONF_CLIENT_UUID) or uuid.uuid4())
        self.token: str | None = data.get(CONF_TOKEN)
        self.token_valid_until: str | None = data.get(CONF_TOKEN_VALID_UNTIL)

        self.structure: LoxoneStructure | None = None
        self.icon_paths: frozenset[str] = frozenset()
        self.state_values: dict[str, Any] = {}
        self.available = False
        configured_server_model = str(
            data.get(CONF_SERVER_MODEL, DEFAULT_SERVER_MODEL)
        ).strip()
        self.server_model = configured_server_model or DEFAULT_SERVER_MODEL
        self.miniserver_name = f"Loxone {self.server_model}"

        self._listeners: dict[Callable[[], None], set[str] | None] = {}
        self._command_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._session: ClientSession = async_get_clientsession(
            hass, verify_ssl=self.verify_ssl
        )
        self._legacy_client: ClientSession | None = None
        self._remote_client: ClientSession | None = None
        self._legacy_session: LegacySession | None = None
        self._ws = None
        self._pending_response: asyncio.Future[str] | None = None
        self._pending_header: PendingHeader | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._system_stats_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._keepalive_waiter: asyncio.Future[bool] | None = None
        self._system_stats_lock = asyncio.Lock()
        self._system_stats_last_refresh = 0.0
        self._closing = False
        self._unavailable_logged = False

    async def async_initialize(self, *, probe_legacy: bool = False) -> None:
        """Connect and load structure, optionally detecting the legacy transport."""
        _LOGGER.debug("Loxone bridge: initialize start")
        await self._ensure_connected(load_structure=True, probe_legacy=probe_legacy)
        _LOGGER.debug("Loxone bridge: initialize complete")

    async def _async_probe_legacy_endpoint(self) -> Mapping[str, Any] | None:
        """Identify a legacy endpoint without attempting authentication over HTTP."""
        if "@" in self.host:
            # An embedded username or password would turn this into an authenticated request.
            return None

        url = f"http://{self.host}:{self.port}{self._ws_path_prefix}{PROBE_PATH}"
        try:
            # A fresh session keeps cookies from the shared Home Assistant session out of the probe.
            async with ClientSession() as session:
                payload = await asyncio.wait_for(
                    _fetch_probe_payload(session, url, ssl=False, allow_redirects=False),
                    timeout=LEGACY_PROBE_TIMEOUT,
                )
        except (ClientError, TimeoutError, OSError, ValueError):
            return None
        return _parse_legacy_probe(payload)

    async def async_shutdown(self) -> None:
        """Stop tasks and close the websocket."""
        self._closing = True

        await self._cleanup_connection(cancel_reconnect=True)

        self._mark_unavailable()

    @property
    def controls(self) -> list[LoxoneControl]:
        """Return parsed controls."""
        return self.structure.controls if self.structure else []

    @property
    def controls_by_action(self) -> dict[str, LoxoneControl]:
        """Return controls indexed by action UUID."""
        return self.structure.controls_by_action if self.structure else {}

    @property
    def media_servers_by_uuid_action(self) -> dict[str, LoxoneMediaServer]:
        """Return parsed media servers indexed by action UUID."""
        return (
            self.structure.media_servers_by_uuid_action
            if self.structure
            else {}
        )

    @property
    def operating_modes(self) -> dict[str, str]:
        """Return parsed global operating mode labels keyed by mode id."""
        return self.structure.operating_modes if self.structure else {}

    @property
    def loxone_state_map(self) -> dict[str, Any]:
        """Return the live state dictionary."""
        return self.state_values

    def export_runtime_data(self) -> dict[str, Any]:
        """Persist the connection settings with the token issued for them."""
        return {
            **self._connection_data,
            CONF_CLIENT_UUID: self.client_uuid,
            CONF_SERIAL: self.serial,
            CONF_LOXAPP_VERSION: self.loxapp_version,
            CONF_SOFTWARE_VERSION: self.software_version,
            CONF_SERVER_MODEL: self.server_model,
            CONF_USE_TLS: self.use_tls,
            CONF_TOKEN: self.token,
            CONF_TOKEN_VALID_UNTIL: self.token_valid_until,
        }

    def resolve_http_url(self, value: str | None) -> str | None:
        """Resolve a possibly relative Miniserver URL to an absolute HTTP(S) URL."""
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None

        split = urlsplit(raw)
        if split.scheme in {"http", "https"} and split.netloc:
            return raw

        scheme = "https" if self.use_tls else "http"
        if raw.startswith("//"):
            return f"{scheme}:{raw}"

        path = raw if raw.startswith("/") else f"/{raw}"
        prefix = self._ws_path_prefix.rstrip("/")
        if prefix and path != prefix and not path.startswith(f"{prefix}/"):
            path = f"{prefix}{path}"

        default_port = 443 if self.use_tls else 80
        port_part = "" if self.port == default_port else f":{self.port}"
        return f"{scheme}://{self.host}{port_part}{path}"

    def resolve_icon_proxy_url(self, value: str | None) -> str | None:
        """Resolve one Loxone icon path to a local Home Assistant proxy URL."""
        if not self.use_loxone_icons:
            return None
        return icon_proxy_url(self.serial, value)

    def state_value(self, state_uuid: str | None) -> Any:
        """Return the latest state value for a UUID."""
        if state_uuid is None:
            return None
        return self.state_values.get(normalize_uuid(state_uuid))

    def control_state(self, control: LoxoneControl, state_name: str) -> Any:
        """Return one named state of a control."""
        return self.state_value(control.state_uuid(state_name))

    def system_stat_state_uuid(self, metric_key: str) -> str:
        """Return synthetic state UUID used for system diagnostics metrics."""
        return normalize_uuid(f"{SYSTEM_STATS_STATE_PREFIX}{metric_key}")

    def system_stat_value(self, metric_key: str) -> Any:
        """Return cached value for one system diagnostics metric."""
        return self.state_value(self.system_stat_state_uuid(metric_key))

    def system_stat_command(self, metric_key: str) -> str | None:
        """Return the raw command path for one diagnostics metric."""
        return SYSTEM_STATS_COMMANDS.get(metric_key)

    def control_for_uuid_action(self, uuid_action: str | None) -> LoxoneControl | None:
        """Return a control by uuidAction."""
        if uuid_action is None:
            return None
        return self.controls_by_action.get(uuid_action)

    def add_listener(
        self, callback_fn: Callable[[], None], watched_uuids: Iterable[str] | None = None
    ) -> Callable[[], None]:
        """Register a callback for state or availability changes."""
        uuids = {normalize_uuid(value) for value in watched_uuids or [] if value}
        self._listeners[callback_fn] = uuids or None

        @callback
        def remove_listener() -> None:
            self._listeners.pop(callback_fn, None)

        return remove_listener

    async def async_send_action(self, uuid_action: str, command: str) -> dict[str, Any]:
        """Send a control command through the authenticated websocket."""
        encoded_uuid = quote(uuid_action, safe="-")
        encoded_command = quote(command, safe="/(),:%._-")
        return await self._send_loxone_command(
            f"jdev/sps/io/{encoded_uuid}/{encoded_command}"
        )

    async def async_send_raw_command(self, command: str) -> dict[str, Any]:
        """Send a raw command through the authenticated websocket."""
        return await self._send_loxone_command(command)

    async def async_refresh_system_stats(
        self,
        *,
        force: bool = False,
        ensure_connected: bool = True,
    ) -> dict[str, Any]:
        """Fetch and cache diagnostics from `dev/sys/*` commands."""
        now = monotonic()
        if (
            not force
            and self._system_stats_last_refresh
            and now - self._system_stats_last_refresh < SYSTEM_STATS_MIN_REFRESH_SECONDS
        ):
            return {
                key: self.system_stat_value(key)
                for key in SYSTEM_STATS_COMMANDS
            }

        async with self._system_stats_lock:
            now = monotonic()
            if (
                not force
                and self._system_stats_last_refresh
                and now - self._system_stats_last_refresh < SYSTEM_STATS_MIN_REFRESH_SECONDS
            ):
                return {
                    key: self.system_stat_value(key)
                    for key in SYSTEM_STATS_COMMANDS
                }

            changed: dict[str, Any] = {}
            for metric_key, command in SYSTEM_STATS_COMMANDS.items():
                try:
                    response = await self._send_loxone_command(
                        command,
                        ensure_connected=ensure_connected,
                    )
                except LoxoneError as err:
                    _LOGGER.debug(
                        "Could not refresh system diagnostics metric %s via %s: %s",
                        metric_key,
                        command,
                        err,
                    )
                    continue
                changed[self.system_stat_state_uuid(metric_key)] = response.get("value")

            self._system_stats_last_refresh = monotonic()
            if changed:
                self._merge_changed_states(changed)
            return {
                key: self.system_stat_value(key)
                for key in SYSTEM_STATS_COMMANDS
            }

    async def _ensure_connected(
        self, load_structure: bool = False, *, probe_legacy: bool = False
    ) -> None:
        async with self._connect_lock:
            if self._closing:
                raise LoxoneConnectionError("The Loxone bridge is shutting down.")
            if self._ws is not None and not self._ws.closed:
                if load_structure and self.structure is None:
                    await self._load_structure()
                return

            if self._reader_task is not None:
                await self._cleanup_connection(cancel_reconnect=False)
            await self._connect(load_structure=load_structure, probe_legacy=probe_legacy)

    async def _open_websocket(
        self,
        session: ClientSession,
        *,
        use_tls: bool,
        target: tuple[str, int, str] | None = None,
    ) -> None:
        scheme = "wss" if use_tls else "ws"
        ssl_verification = None if self.verify_ssl else False
        host, port, prefix = target or (self.host, self.port, self._ws_path_prefix)
        url = f"{scheme}://{host}:{port}{prefix}{WS_PATH}"

        _LOGGER.debug("Connecting to Loxone websocket")
        try:
            websocket = await session.ws_connect(
                url,
                protocols=("remotecontrol",),
                ssl=ssl_verification,
                heartbeat=None,
            )
        except (ClientError, TimeoutError, OSError) as err:
            raise _LoxoneWebSocketConnectionError(
                "Could not connect to the configured Loxone host."
            ) from err
        if self._closing:
            await websocket.close()
            raise LoxoneConnectionError("The Loxone bridge is shutting down.")
        self._ws = websocket
        _LOGGER.debug("Loxone bridge: websocket connected")

    async def _open_remote_websocket(self, serial: str) -> None:
        from aiohttp import TraceConfig

        async def reject_redirect(*_args: Any) -> None:
            # A websocket upgrade must not redirect authentication to a different endpoint.
            raise LoxoneConnectionError("The Loxone Remote Connect relay redirected the connection.")

        trace = TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        client = self._remote_client = ClientSession(trace_configs=[trace])
        try:
            async with asyncio.timeout(REMOTE_CONNECT_TIMEOUT):
                async with client.get(
                    f"https://{REMOTE_CONNECT_HOST}/getip",
                    params={"snr": serial},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise LoxoneConnectionError(
                            f"Loxone Remote Connect lookup failed (HTTP {response.status})."
                        )
                    relay = _ensure_mapping(await response.json(content_type=None)).get("url")
        except (ClientError, TimeoutError, OSError, ValueError) as err:
            raise LoxoneConnectionError("Could not look up the Loxone Remote Connect relay.") from err

        if self._closing:
            raise LoxoneConnectionError("The Loxone bridge is shutting down.")
        target = _remote_relay_target(relay)
        await self._open_websocket(client, use_tls=True, target=target)
        self.host, self.port, self._ws_path_prefix = target

    async def _open_legacy_websocket(self, expected_serial: str | None) -> LegacySession:
        if "@" in self.host:
            raise LoxoneConnectionError("Enter the Miniserver hostname without credentials.")

        # Keep the credentials and cookies of the shared HA session out of HTTP and WS.
        client = self._legacy_client = ClientSession()
        url = f"http://{self.host}:{self.port}{self._ws_path_prefix}{LEGACY_CERTIFICATE_PATH}"
        try:
            async with asyncio.timeout(LEGACY_CERTIFICATE_TIMEOUT):
                async with client.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise LoxoneUnsupportedError(
                            "The Gen1 Miniserver did not provide a verifiable certificate chain."
                        )
                    certificate = await response.text()
        except (ClientError, TimeoutError, OSError, ValueError) as err:
            raise LoxoneConnectionError("Could not read the Gen1 Miniserver certificate.") from err

        # Firmware may return the PEM directly or put it in an LL JSON envelope.
        try:
            if not certificate.lstrip().startswith("-----BEGIN CERTIFICATE-----"):
                certificate = _parse_loxone_command_payload(certificate, LEGACY_CERTIFICATE_PATH)[
                    "value"
                ]
            if not isinstance(certificate, str):
                raise LegacyCryptoError("Expected a PEM certificate chain.")
            legacy_session = LegacySession.from_certificate_chain(
                certificate, expected_serial=expected_serial
            )
        except (LegacyCryptoError, LoxoneConnectionError, LoxoneAuthenticationError) as err:
            raise LoxoneUnsupportedError(
                "The Gen1 Miniserver certificate could not be verified."
            ) from err

        await self._open_websocket(client, use_tls=False)
        return legacy_session

    async def _connect(self, load_structure: bool, *, probe_legacy: bool = False) -> None:
        legacy_session = None
        try:
            if self._remote_serial:
                await self._open_remote_websocket(self._remote_serial)
            elif self.use_tls:
                try:
                    await self._open_websocket(self._session, use_tls=True)
                except _LoxoneWebSocketConnectionError as err:
                    if not probe_legacy or isinstance(
                        err.__cause__, ssl.SSLCertVerificationError
                    ):
                        raise
                    metadata = await self._async_probe_legacy_endpoint()
                    if metadata is None:
                        raise
                    version = _extract_software_version(metadata)
                    _ensure_supported_miniserver_version(version)
                    legacy_session = await self._open_legacy_websocket(
                        _normalize_mac(self.serial) or _normalize_mac(str(metadata["snr"]))
                    )
                    self.software_version = version
                    self.use_tls = False
            else:
                legacy_session = await self._open_legacy_websocket(_normalize_mac(self.serial))

            self._pending_header = None
            self._reader_task = self.hass.async_create_background_task(
                self._reader_loop(), "loxone_ws_reader"
            )
            self._keepalive_task = self.hass.async_create_background_task(
                self._keepalive_loop(), "loxone_keepalive"
            )

            _LOGGER.debug("Loxone bridge: authenticate start")
            if legacy_session is None:
                await self._authenticate()
            else:
                challenge = await self._exchange_legacy_key(legacy_session)
                await self._authenticate(legacy_challenge=challenge)
            _LOGGER.debug("Loxone bridge: authenticate complete")
            if load_structure or self.structure is None:
                _LOGGER.debug("Loxone bridge: loading structure")
                await self._load_structure()
                _LOGGER.debug("Loxone bridge: structure loaded")
            _LOGGER.debug("Loxone bridge: enabling status updates")
            await self._send_loxone_command(
                "jdev/sps/enablebinstatusupdate",
                ensure_connected=False,
            )
            _LOGGER.debug("Loxone bridge: status updates enabled")
            await self.async_refresh_system_stats(
                force=True,
                ensure_connected=False,
            )
            self._ensure_system_stats_task()
        except (Exception, asyncio.CancelledError):
            await self._cleanup_connection(cancel_reconnect=False)
            raise

        self._mark_available()

    async def _exchange_legacy_key(self, session: LegacySession) -> bytes:
        try:
            response = await self._send_loxone_command(
                session.key_exchange_command(), ensure_connected=False
            )
            challenge = bytes.fromhex(session.decrypt_response(response["value"]))
        except (TypeError, ValueError) as err:
            raise LoxoneConnectionError("The Gen1 Miniserver rejected the key exchange.") from err
        if not challenge or response["code"] != 200:
            raise LoxoneConnectionError("The Gen1 Miniserver returned an invalid key challenge.")
        self._legacy_session = session
        return challenge

    async def _authenticate(self, *, legacy_challenge: bytes | None = None) -> None:
        auth_username = _auth_path_segment(self.username)
        encoded_username = quote(auth_username, safe="")
        legacy = legacy_challenge is not None
        software_version = parse_miniserver_version(self.software_version)
        if self.token and not self._token_expired() and (not legacy or software_version):
            token_proof = self.token
            if legacy:
                # Loxone's SDK uses SHA256 for token challenges starting in firmware 10.4.
                algorithm = hashlib.sha256 if software_version >= (10, 4) else hashlib.sha1
                token_proof = hmac.new(legacy_challenge, self.token.encode(), algorithm).hexdigest()
            try:
                await self._send_loxone_command(
                    f"authwithtoken/{quote(token_proof, safe='')}/{encoded_username}",
                    ensure_connected=False,
                    encrypt_response=legacy,
                )
                return
            except LoxoneAuthenticationError:
                _LOGGER.info("Stored Loxone token rejected, falling back to username/password.")
                self.token = None
                self.token_valid_until = None

        key_response = await self._send_loxone_command(
            f"jdev/sys/getkey2/{encoded_username}",
            ensure_connected=False,
        )
        value = _ensure_mapping(key_response.get("value"))
        hash_algorithm = str(value.get("hashAlg") or ("SHA1" if legacy else "SHA256")).upper()
        try:
            password_hash = _hash_password(self.password, str(value["salt"]), hash_algorithm)
            final_hash = _hmac_user_hash(
                auth_username, password_hash, str(value["key"]), hash_algorithm
            )
        except (KeyError, ValueError) as err:
            raise LoxoneConnectionError(
                "The Miniserver returned an invalid password challenge."
            ) from err

        client_uuid = self.client_uuid
        if legacy:
            parts = client_uuid.split("-")
            if len(parts) == 5:
                client_uuid = "-".join((*parts[:3], parts[3] + parts[4]))
        for permission in (APP_PERMISSION, WEB_PERMISSION):
            try:
                token_response = await self._send_loxone_command(
                    "jdev/sys/getjwt/"
                    f"{final_hash}/{encoded_username}/{permission}/"
                    f"{quote(client_uuid, safe='-')}/{quote(CLIENT_INFO, safe='')}",
                    ensure_connected=False,
                    encrypt_response=legacy,
                )
            except LoxoneAuthenticationError:
                if permission == WEB_PERMISSION:
                    raise
                _LOGGER.info("JWT auth with app permission failed, falling back to web permission.")
            else:
                break

        token_value = _ensure_mapping(token_response.get("value"))
        token_raw = token_value.get("token")
        if not token_raw or (legacy and token_response["code"] != 200):
            raise LoxoneAuthenticationError("Authentication failed.")
        self.token = str(token_raw)
        valid_until = token_value.get("validUntil")
        self.token_valid_until = str(valid_until) if valid_until is not None else None

    async def _load_structure(self) -> None:
        raw = await self._send_text_command("data/LoxAPP3.json", ensure_connected=False)
        try:
            structure_payload = json.loads(raw)
        except json.JSONDecodeError as err:
            raise LoxoneConnectionError("Invalid LoxAPP3.json payload received.") from err
        if not isinstance(structure_payload, Mapping):
            raise LoxoneConnectionError("Invalid LoxAPP3.json payload received.")

        _raise_for_ll_error_payload(structure_payload)
        if not _is_loxapp_structure_payload(structure_payload):
            raise LoxoneConnectionError("Unexpected LoxAPP3.json payload.")

        structure = parse_structure(structure_payload)
        serial = structure.serial
        if self._legacy_session is not None:
            certified_serial = self._legacy_session.serial
            if _normalize_mac(serial) not in (None, certified_serial):
                raise LoxoneConnectionError("The Gen1 structure does not match its certificate.")
            serial = certified_serial
        self.structure = structure
        self.icon_paths = frozenset(
            icon_path
            for control in structure.controls
            if (icon_path := normalize_icon_path(control.icon)) is not None
        )
        self.serial = serial
        self.loxapp_version = self.structure.loxapp_version
        self.server_model = self.structure.server_model
        self.miniserver_name = self.structure.miniserver_name

        ms_info = _ensure_mapping(structure_payload.get("msInfo"))
        version = _extract_software_version(ms_info)
        if version is None:
            version = await self._async_fetch_software_version()
        if version is not None:
            self.software_version = version

        _ensure_supported_miniserver_version(self.software_version)

    async def _async_fetch_software_version(self) -> str | None:
        """Try to read Miniserver software version from `jdev/cfg/apiKey`."""
        try:
            response = await self._send_loxone_command(
                "jdev/cfg/apiKey",
                ensure_connected=False,
            )
        except LoxoneError as err:
            _LOGGER.debug("Could not fetch Miniserver software version: %s", err)
            return None
        return _extract_software_version(_ensure_mapping(response.get("value")))

    async def _send_loxone_command(
        self, command: str, *, ensure_connected: bool = True, encrypt_response: bool = False
    ) -> dict[str, Any]:
        if encrypt_response:
            raw = await self._send_text_command(
                command, ensure_connected=ensure_connected, encrypt_response=True
            )
        else:
            raw = await self._send_text_command(command, ensure_connected=ensure_connected)
        return _parse_loxone_command_payload(raw, command)

    async def _send_text_command(
        self, command: str, *, ensure_connected: bool = True, encrypt_response: bool = False
    ) -> str:
        if ensure_connected:
            await self._ensure_connected(load_structure=False)
        async with self._command_lock:
            websocket = self._ws
            if websocket is None or websocket.closed:
                raise LoxoneConnectionError("Websocket is not connected.")
            legacy_session = self._legacy_session
            if not self.use_tls and legacy_session is None and not command.startswith(
                "jdev/sys/keyexchange/"
            ):
                raise LoxoneConnectionError("The encrypted Gen1 session is no longer connected.")
            loop = asyncio.get_running_loop()
            self._pending_response = loop.create_future()
            try:
                wire_command = command
                if legacy_session is not None:
                    wire_command = legacy_session.encrypt_command(
                        command, encrypt_response=encrypt_response
                    )
                await websocket.send_str(wire_command)
                response = await asyncio.wait_for(self._pending_response, timeout=20)
                if encrypt_response and legacy_session is not None:
                    if response.lstrip().startswith("{"):
                        _raise_for_ll_error_payload(_deserialize_json(response))
                        raise LoxoneConnectionError("Expected an encrypted Gen1 response.")
                    return legacy_session.decrypt_response(response)
                return response
            except asyncio.TimeoutError as err:
                raise LoxoneConnectionError(
                    "No response for Loxone command."
                ) from err
            except (ClientError, LegacyCryptoError, OSError) as err:
                raise LoxoneConnectionError("Could not complete the Miniserver command.") from err
            finally:
                self._pending_response = None

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        loop_error: Exception | None = None
        try:
            async for message in self._ws:
                await self._handle_ws_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            loop_error = err
            _LOGGER.debug("Loxone websocket reader stopped: %s", err)

        if not self._closing:
            self._mark_unavailable(
                reason="websocket reader stopped",
                err=loop_error,
            )
            await self._cleanup_connection(cancel_reconnect=False)
            self._schedule_reconnect()

    async def _handle_ws_message(self, message: WSMessage) -> None:
        if message.type == WSMsgType.TEXT:
            self._deliver_text(message.data)
            return

        if message.type == WSMsgType.BINARY:
            await self._handle_binary_message(message.data)
            return

        if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
            raise LoxoneConnectionError("Websocket closed by the Miniserver.")

    async def _handle_binary_message(self, data: bytes) -> None:
        if len(data) == 8 and data[0] == 0x03:
            header = parse_header(data)
            # Some Miniservers send state-table headers with the "estimated"
            # bit set; dropping them causes all runtime states to stay unknown.
            if header.identifier == KEEPALIVE_RESPONSE:
                if self._keepalive_waiter and not self._keepalive_waiter.done():
                    self._keepalive_waiter.set_result(True)
                return
            if header.identifier == OUT_OF_SERVICE:
                raise LoxoneConnectionError("Miniserver reported out-of-service.")
            self._pending_header = header
            return

        header = self._pending_header
        self._pending_header = None
        if header is None:
            return

        if header.identifier == VALUE_STATE_TABLE:
            changed = parse_value_state_table(data)
            self._merge_changed_states(changed)
            return

        if header.identifier == TEXT_STATE_TABLE:
            changed = parse_text_state_table(data)
            self._merge_changed_states(changed)
            return

        if header.identifier in (TEXT_MESSAGE, FILE_MESSAGE):
            self._deliver_text(data.decode("utf-8", errors="ignore"))

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(DEFAULT_KEEPALIVE_SECONDS)
                if self._ws is None or self._ws.closed:
                    return

                loop = asyncio.get_running_loop()
                self._keepalive_waiter = loop.create_future()
                await self._ws.send_str("keepalive")
                try:
                    await asyncio.wait_for(self._keepalive_waiter, timeout=20)
                except asyncio.TimeoutError as err:
                    raise LoxoneConnectionError("Loxone keepalive timed out.") from err
                finally:
                    self._keepalive_waiter = None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Loxone keepalive loop failed: %s", err)
            self._mark_unavailable(reason="keepalive loop failed", err=err)
            if self._ws is not None and not self._ws.closed:
                with contextlib.suppress(ClientError, RuntimeError):
                    await self._ws.close()

    def _ensure_system_stats_task(self) -> None:
        if self._system_stats_task and not self._system_stats_task.done():
            return
        self._system_stats_task = self.hass.async_create_background_task(
            self._system_stats_loop(),
            "loxone_system_stats",
        )

    async def _system_stats_loop(self) -> None:
        try:
            while True:
                if self.available:
                    try:
                        await self.async_refresh_system_stats(force=True, ensure_connected=False)
                    except LoxoneError as err:
                        _LOGGER.debug("System diagnostics refresh failed: %s", err)
                await asyncio.sleep(SYSTEM_STATS_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise

    def _schedule_reconnect(self) -> None:
        if self._closing or (self._reconnect_task and not self._reconnect_task.done()):
            return
        self._reconnect_task = self.hass.async_create_background_task(
            self._reconnect_loop(),
            "loxone_reconnect",
        )

    async def _reconnect_loop(self) -> None:
        delay = 5
        while not self._closing:
            await asyncio.sleep(delay)
            try:
                await self._ensure_connected(load_structure=self.structure is None)
            except LoxoneError as err:
                _LOGGER.debug("Reconnect to Loxone failed: %s", err)
                delay = min(delay * 2, 60)
                continue
            return

    def _mark_available(self) -> None:
        """Set bridge as available and emit one recovery log if needed."""
        was_available = self.available
        self.available = True
        if self._unavailable_logged:
            _LOGGER.info("Loxone connection restored.")
            self._unavailable_logged = False
        if not was_available:
            self._notify_listeners()

    def _mark_unavailable(
        self,
        *,
        reason: str | None = None,
        err: Exception | None = None,
    ) -> None:
        """Set bridge as unavailable and log transition once."""
        was_available = self.available
        self.available = False
        if was_available:
            self._notify_listeners()
        if reason is None or self._unavailable_logged:
            return
        if err is None:
            _LOGGER.info("Loxone connection unavailable: %s", reason)
        else:
            _LOGGER.info("Loxone connection unavailable (%s): %s", reason, err)
        self._unavailable_logged = True

    def _token_expired(self) -> bool:
        if not self.token_valid_until:
            return False
        valid_until = str(self.token_valid_until)
        now = datetime.now(UTC)
        try:
            if valid_until.isdecimal():
                return int(valid_until) <= (now - LOXONE_EPOCH).total_seconds()
            expires = datetime.fromisoformat(valid_until)
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= now

    async def _cleanup_connection(
        self, cancel_reconnect: bool, close_ws: bool = True
    ) -> None:
        current_task = asyncio.current_task()

        for task in (self._keepalive_task, self._reader_task, self._system_stats_task):
            if task and task is not current_task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        if cancel_reconnect and self._reconnect_task and self._reconnect_task is not current_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self._keepalive_waiter and not self._keepalive_waiter.done():
            self._keepalive_waiter.cancel()
        self._keepalive_waiter = None

        if self._pending_response and not self._pending_response.done():
            self._pending_response.set_exception(LoxoneConnectionError("Websocket is not connected."))

        if close_ws and self._ws is not None and not self._ws.closed:
            with contextlib.suppress(ClientError, RuntimeError):
                await self._ws.close()

        self._ws = None
        self._legacy_session = None
        private_clients = (self._legacy_client, self._remote_client)
        self._legacy_client = self._remote_client = None
        for client in private_clients:
            if client is not None:
                await client.close()
        if self._reader_task is current_task:
            self._reader_task = None
        elif self._reader_task and self._reader_task.done():
            self._reader_task = None
        self._keepalive_task = None
        self._system_stats_task = None
        if cancel_reconnect:
            self._reconnect_task = None

    def _deliver_text(self, payload: str) -> None:
        is_state_update, has_response_code = self._apply_state_update_from_text(payload)
        if self._pending_response and not self._pending_response.done():
            if not is_state_update or has_response_code:
                self._pending_response.set_result(payload)

    def _apply_state_update_from_text(self, payload: str) -> tuple[bool, bool]:
        try:
            root = _ensure_mapping(_deserialize_json(payload).get("LL"))
        except (json.JSONDecodeError, LoxoneConnectionError):
            return False, False

        has_response_code = "Code" in root or "code" in root
        control = root.get("control")
        if not isinstance(control, str):
            return False, has_response_code

        state_uuid = _state_uuid_from_control_path(control)
        if state_uuid is None:
            return False, has_response_code

        self._merge_changed_states({state_uuid: deserialize_value(root.get("value"))})
        return True, has_response_code

    def _merge_changed_states(self, changed: Mapping[str, Any]) -> None:
        if not changed:
            return
        normalized = {
            normalize_uuid(state_uuid): value
            for state_uuid, value in changed.items()
            if isinstance(state_uuid, str) and state_uuid
        }
        if not normalized:
            return
        previous_values = {
            state_uuid: self.state_values.get(state_uuid)
            for state_uuid in normalized
        }
        self.state_values.update(normalized)
        self._emit_intercom_events(normalized, previous_values)
        self._emit_access_events(normalized, previous_values)
        self._notify_listeners(set(normalized))

    def _emit_intercom_events(
        self,
        changed: Mapping[str, Any],
        previous_values: Mapping[str, Any],
    ) -> None:
        structure = getattr(self, "structure", None)
        if structure is None:
            return

        hass = getattr(self, "hass", None)
        bus = getattr(hass, "bus", None)
        fire_event = getattr(bus, "async_fire", None)
        if fire_event is None:
            return

        for state_uuid, new_value in changed.items():
            state_ref = structure.states.get(state_uuid)
            if state_ref is None:
                continue
            control = self.controls_by_action.get(state_ref.control_uuid_action)
            if control is None or not is_intercom_control(control):
                continue

            event_name = _intercom_event_name_for_state(control, state_ref.state_name)
            if event_name is None:
                continue

            if not _state_triggered(previous_values.get(state_uuid), new_value):
                continue

            event_data: dict[str, Any] = {
                "serial": self.serial,
                "uuid_action": control.uuid_action,
                "control_name": control.display_name,
                "control_type": control.type,
                "room": control.room_name,
                "category": control.category_name,
                "state_name": state_ref.state_name,
                "event": event_name,
                "value": new_value,
            }
            fire_event(EVENT_INTERCOM, event_data)

    def _emit_access_events(
        self,
        changed: Mapping[str, Any],
        previous_values: Mapping[str, Any],
    ) -> None:
        structure = getattr(self, "structure", None)
        if structure is None:
            return

        hass = getattr(self, "hass", None)
        bus = getattr(hass, "bus", None)
        fire_event = getattr(bus, "async_fire", None)
        if fire_event is None:
            return

        for state_uuid, new_value in changed.items():
            state_ref = structure.states.get(state_uuid)
            if state_ref is None:
                continue
            control = self.controls_by_action.get(state_ref.control_uuid_action)
            if control is None or not _is_access_event_control(control):
                continue

            event_name = _access_event_name_for_state_and_value(
                control,
                state_ref.state_name,
                new_value,
            )
            if event_name is None:
                continue

            previous_value = previous_values.get(state_uuid)
            if not _state_triggered_or_changed(previous_value, new_value):
                if not _should_force_access_event(event_name, new_value):
                    continue

            event_data: dict[str, Any] = {
                "serial": self.serial,
                "uuid_action": control.uuid_action,
                "control_name": control.display_name,
                "control_type": control.type,
                "room": control.room_name,
                "category": control.category_name,
                "state_name": state_ref.state_name,
                "event": event_name,
                "value": new_value,
            }
            fire_event(EVENT_ACCESS, event_data)

    @callback
    def _notify_listeners(self, changed_uuids: set[str] | None = None) -> None:
        for callback_fn, watched in list(self._listeners.items()):
            if changed_uuids is None or watched is None or watched.intersection(changed_uuids):
                try:
                    callback_fn()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Loxone listener callback failed; keeping websocket connection alive."
                    )


def _intercom_event_name_for_state(control: LoxoneControl, state_name: str) -> str | None:
    if state_name == intercom_doorbell_state_name(control):
        return "doorbell"
    if state_name == intercom_call_state_name(control):
        return "call"
    if state_name == intercom_proximity_state_name(control):
        return "proximity"
    if state_name == intercom_light_state_name(control):
        return "light"

    normalized_state_name = _normalize_state_name(state_name)
    if "bell" in normalized_state_name or "ring" in normalized_state_name:
        return "doorbell"
    if "call" in normalized_state_name or "talk" in normalized_state_name:
        return "call"
    if "prox" in normalized_state_name or "near" in normalized_state_name:
        return "proximity"
    if "light" in normalized_state_name or "led" in normalized_state_name:
        return "light"
    return None


def _is_access_event_control(control: LoxoneControl) -> bool:
    normalized_type = control.type.casefold()
    normalized_name = control.name.casefold()
    if any(hint in normalized_type or hint in normalized_name for hint in ACCESS_TYPE_HINT_KEYS):
        return True

    normalized_states = {_normalize_state_name(state_name) for state_name in control.states}
    if any(
        _state_name_matches_candidate_set(state_name, ACCESS_GRANTED_STATE_CANDIDATES)
        for state_name in normalized_states
    ):
        return True
    if any(
        _state_name_matches_candidate_set(state_name, ACCESS_DENIED_STATE_CANDIDATES)
        for state_name in normalized_states
    ):
        return True
    return any(
        any(hint in state_name for hint in ACCESS_EVENT_DETAIL_HINTS)
        for state_name in normalized_states
    )


def _access_event_name_for_state(control: LoxoneControl, state_name: str) -> str | None:
    del control
    normalized_state_name = _normalize_state_name(state_name)
    event_name: str | None = None
    if _state_name_matches_candidate_set(normalized_state_name, ACCESS_DENIED_STATE_CANDIDATES):
        event_name = "access_denied"
    elif _state_name_matches_candidate_set(normalized_state_name, ACCESS_GRANTED_STATE_CANDIDATES):
        event_name = "access_granted"
    elif any(hint in normalized_state_name for hint in ACCESS_DENIED_EVENT_HINTS):
        event_name = "access_denied"
    elif any(hint in normalized_state_name for hint in ACCESS_GRANTED_EVENT_HINTS):
        event_name = "access_granted"
    elif any(hint in normalized_state_name for hint in ACCESS_EVENT_DETAIL_HINTS):
        event_name = "access_activity"
    return event_name


def _access_event_name_for_state_and_value(
    control: LoxoneControl,
    state_name: str,
    value: Any,
) -> str | None:
    event_name = _access_event_name_for_state(control, state_name)
    inferred_from_value = _access_event_name_from_value(value)
    if inferred_from_value is None:
        return event_name
    if event_name is None or event_name == "access_activity":
        return inferred_from_value
    if event_name != inferred_from_value:
        return inferred_from_value
    return event_name


def _access_event_name_from_value(value: Any) -> str | None:
    for normalized_text in _iter_access_value_text_tokens(value):
        if any(hint and hint in normalized_text for hint in ACCESS_DENIED_VALUE_HINTS):
            return "access_denied"
        if any(hint and hint in normalized_text for hint in ACCESS_GRANTED_VALUE_HINTS):
            return "access_granted"
    return None


def _iter_access_value_text_tokens(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        return (_normalize_state_name(stripped),)
    if isinstance(value, Mapping):
        tokens: list[str] = []
        preferred_keys = (
            "value",
            "state",
            "event",
            "result",
            "reason",
            "message",
            "text",
            "description",
            "authType",
            "keyPadAuthType",
        )
        seen_keys: set[str] = set()
        for key in preferred_keys:
            nested = value.get(key, value.get(key.casefold()))
            seen_keys.add(key.casefold())
            for token in _iter_access_value_text_tokens(nested):
                if token:
                    tokens.append(token)
        for nested_key, nested_value in value.items():
            if isinstance(nested_key, str) and nested_key.casefold() in seen_keys:
                continue
            for token in _iter_access_value_text_tokens(nested_value):
                if token:
                    tokens.append(token)
        return tuple(tokens)
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            for token in _iter_access_value_text_tokens(item):
                if token:
                    tokens.append(token)
        return tuple(tokens)
    return (_normalize_state_name(str(value)),)


def _state_name_matches_candidate_set(normalized_state_name: str, candidates: tuple[str, ...]) -> bool:
    return any(normalized_state_name == _normalize_state_name(candidate) for candidate in candidates)


def _state_triggered_or_changed(previous_value: Any, new_value: Any) -> bool:
    current_bool = _coerce_event_bool(new_value)
    if current_bool is not None:
        previous_bool = _coerce_event_bool(previous_value)
        if previous_bool is None:
            return current_bool
        return not previous_bool and current_bool

    if new_value is None:
        return False
    return previous_value != new_value


def _should_force_access_event(event_name: str, new_value: Any) -> bool:
    if event_name not in {"access_denied", "access_granted"}:
        return False

    current_bool = _coerce_event_bool(new_value)
    if current_bool is not None:
        return current_bool

    if isinstance(new_value, str):
        return bool(new_value.strip())
    if isinstance(new_value, (int, float)):
        return new_value != 0
    if isinstance(new_value, Mapping):
        nested_value = new_value.get("value", new_value.get("state", new_value.get("active")))
        return _should_force_access_event(event_name, nested_value)
    return new_value is not None


def _state_triggered(previous_value: Any, new_value: Any) -> bool:
    previous_bool = _coerce_event_bool(previous_value)
    current_bool = _coerce_event_bool(new_value)
    if current_bool is None:
        return False
    if previous_bool is None:
        return current_bool
    return not previous_bool and current_bool


def _coerce_event_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "on", "yes", "open"}:
            return True
        if lowered in {"0", "false", "off", "no", "closed"}:
            return False
    if isinstance(value, Mapping):
        nested_value = value.get("value", value.get("active", value.get("state")))
        return _coerce_event_bool(nested_value)
    return None


def _normalize_state_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


async def async_discover_miniservers(
    hass: HomeAssistant, timeout: int = DEFAULT_SCAN_TIMEOUT
) -> DiscoverySummary:
    """Scan the local network for Loxone Miniservers."""
    candidates = _candidate_hosts(await async_get_adapters(hass))
    if not candidates:
        return DiscoverySummary()

    async with ClientSession() as session:
        devices = await _async_probe_hosts(
            session,
            candidates,
            timeout=timeout,
            probe_legacy=True,
        )
    devices.sort(key=lambda item: item.label.lower())
    return DiscoverySummary(
        devices=devices, legacy_found=any(not item.use_tls for item in devices)
    )


async def _async_probe_hosts(
    session: ClientSession,
    hosts: Iterable[str],
    timeout: int,
    *,
    probe_legacy: bool,
) -> list[DiscoveryResult]:
    tasks = [
        asyncio.create_task(_probe_host(session, host, timeout, probe_legacy=probe_legacy))
        for host in hosts
    ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    devices: list[DiscoveryResult] = []
    for result in results:
        if isinstance(result, DiscoveryResult):
            devices.append(result)

    return devices


async def _probe_host(
    session: ClientSession,
    host: str,
    timeout: int,
    *,
    probe_legacy: bool,
) -> DiscoveryResult | None:
    probe_variants = [("https", 443, timeout)]
    if probe_legacy:
        probe_variants.append(("http", 80, min(timeout, LEGACY_PROBE_TIMEOUT)))

    for scheme, port, probe_timeout in probe_variants:
        url = f"{scheme}://{host}:{port}{PROBE_PATH}"
        use_tls = scheme == "https"
        try:
            payload = await asyncio.wait_for(
                _fetch_probe_payload(session, url, ssl=False, allow_redirects=use_tls),
                timeout=probe_timeout,
            )
        except (ClientError, TimeoutError, OSError, ValueError):
            continue
        if payload is None:
            continue

        api_payload = parse_api_key_payload(payload) if use_tls else _parse_legacy_probe(payload)
        if api_payload is None:
            continue

        version = _extract_software_version(api_payload)
        server_model = detect_server_model_from_mapping(api_payload)
        name = (
            api_payload.get("name")
            or api_payload.get("msName")
            or api_payload.get("snr")
            or version
            or host
        )
        label = _build_discovery_label(str(name), host, server_model)
        return DiscoveryResult(
            host=host,
            port=port,
            use_tls=use_tls,
            label=label,
            server_model=server_model,
        )

    return None


def _parse_legacy_probe(payload: Any) -> Mapping[str, Any] | None:
    """Require a successful Loxone response identifying a non-TLS device."""
    api_payload = parse_api_key_payload(payload)
    if api_payload is None:
        return None
    root = payload["LL"]
    if (
        _coerce_response_code(root.get("Code") or root.get("code")) == 200
        and _normalize_mac(str(api_payload.get("snr") or "")) is not None
        and parse_miniserver_version(_extract_software_version(api_payload)) is not None
        and api_payload.get("httpsStatus") in (None, 0, "0")
    ):
        return api_payload
    return None


async def _fetch_probe_payload(
    session: ClientSession, url: str, *, ssl: bool, allow_redirects: bool = True
) -> Any | None:
    async with session.get(url, ssl=ssl, allow_redirects=allow_redirects) as response:
        if response.status != 200:
            return None
        return await response.json(content_type=None)


def _candidate_hosts(adapters: list[Mapping[str, Any]]) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()

    def adapter_priority(adapter: Mapping[str, Any]) -> tuple[int, int, str]:
        return (
            0 if adapter.get("default") else 1,
            0 if adapter.get("auto") else 1,
            str(adapter.get("name", "")),
        )

    for adapter in sorted(adapters, key=adapter_priority):
        if not adapter.get("enabled"):
            continue

        for ipv4 in adapter.get("ipv4", []):
            address = ipv4.get("address")
            prefix = ipv4.get("network_prefix")
            if not address or prefix is None:
                continue

            try:
                interface_address = ipaddress.ip_address(address)
                if interface_address.is_loopback or interface_address.is_link_local:
                    continue
                prefix_int = int(prefix)
                if prefix_int < 24:
                    prefix_int = 24
                network = ipaddress.ip_network(f"{address}/{prefix_int}", strict=False)
            except ValueError:
                continue

            for host in network.hosts():
                host_str = str(host)
                if host_str == address or host_str in seen:
                    continue
                seen.add(host_str)
                hosts.append(host_str)

    return hosts


def _state_uuid_from_control_path(control: str) -> str | None:
    match = _STATE_UPDATE_CONTROL_RE.match(control.strip())
    if not match:
        return None
    return normalize_uuid(match.group("uuid"))


def _deserialize_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as err:
        raise LoxoneConnectionError(
            "Invalid JSON response received from the Miniserver."
        ) from err
    if not isinstance(parsed, dict):
        raise LoxoneConnectionError("Expected a JSON object from the Miniserver.")
    return parsed


def _parse_loxone_command_payload(raw: str, command: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped:
        raise LoxoneConnectionError("Empty response received from the Miniserver.")

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {
            "code": 200,
            "control": command,
            "value": deserialize_value(stripped),
        }

    if isinstance(parsed, Mapping):
        root = parsed.get("LL")
        if isinstance(root, Mapping):
            code = _coerce_response_code(root.get("Code") or root.get("code") or 0)
            if code == 401:
                raise LoxoneAuthenticationError("Authentication failed.")
            if code and code >= 400:
                raise LoxoneConnectionError(
                    f"Loxone command failed with code {code}."
                )
            return {
                "code": code,
                "control": root.get("control") or command,
                "value": deserialize_value(root.get("value")),
            }

        _raise_for_ll_error_payload(parsed)
        return {
            "code": 200,
            "control": command,
            "value": deserialize_value(parsed),
        }

    return {
        "code": 200,
        "control": command,
        "value": deserialize_value(parsed),
    }


def _coerce_response_code(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ensure_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise LoxoneConnectionError("Unexpected Loxone response payload.")


def _hash_password(password: str, salt: str, hash_algorithm: str) -> str:
    payload = f"{password}:{salt}".encode("utf-8")
    algorithm = _resolve_hash_algorithm(hash_algorithm)
    return algorithm(payload).hexdigest().upper()


def _hmac_user_hash(user: str, password_hash: str, key_hex: str, hash_algorithm: str) -> str:
    digest_mod = hashlib.sha256 if "256" in hash_algorithm else hashlib.sha1
    message = f"{user}:{password_hash}".encode("utf-8")
    return hmac.new(bytes.fromhex(key_hex), message, digest_mod).hexdigest()


def _resolve_hash_algorithm(name: str) -> Callable[[bytes], Any]:
    if "256" in name:
        return hashlib.sha256
    return hashlib.sha1


def _auth_path_segment(value: str) -> str:
    """Return safe auth path segment for Loxone commands."""
    segment = value.strip()
    if "/" in segment:
        raise LoxoneAuthenticationError(
            "The configured username contains unsupported '/' character."
        )
    return segment


def _resolve_miniserver_target(raw_host: str) -> tuple[str, str]:
    """Resolve configured host to `(hostname, websocket_path_prefix)`."""
    value = raw_host.strip()
    if not value:
        return value, ""

    parsed = None
    if "://" in value:
        parsed = urlsplit(value)
    elif "/" in value and not value.startswith("/"):
        parsed = urlsplit(f"//{value}")

    if parsed and parsed.hostname:
        host = parsed.hostname
        mac = _extract_mac_from_path(parsed.path)
        if host in {CLOUD_DNS_HOST, REMOTE_CONNECT_HOST} and mac:
            return CLOUD_DNS_HOST, f"/{mac}"
        return host, parsed.path.rstrip("/")

    mac = _normalize_mac(value)
    if mac:
        return CLOUD_DNS_HOST, f"/{mac}"
    return value, ""


def _remote_relay_target(value: Any) -> tuple[str, int, str]:
    """Validate the HTTPS/WSS relay authority and return its base path."""
    error = "Loxone Remote Connect returned an invalid relay URL."
    if not isinstance(value, str):
        raise LoxoneConnectionError(error)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as err:
        raise LoxoneConnectionError(error) from err
    host = parsed.hostname
    if (
        parsed.scheme not in {"https", "wss"}
        or not host
        or not _REMOTE_RELAY_HOST.fullmatch(host)
        or port is None
        or not 20000 <= port <= 49000
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LoxoneConnectionError(error)
    path = parsed.path.rstrip("/").removesuffix(WS_PATH)
    return host, port, path


def _extract_mac_from_path(path: str) -> str | None:
    first_segment = path.strip("/").split("/", 1)[0]
    if not first_segment:
        return None
    return _normalize_mac(first_segment)


def _normalize_mac(value: str) -> str | None:
    candidate = value.strip()
    if _MAC_PLAIN_RE.fullmatch(candidate):
        return candidate.upper()
    if _MAC_SEPARATED_RE.fullmatch(candidate):
        return candidate.replace(":", "").replace("-", "").upper()
    return None


def _raise_for_ll_error_payload(payload: Mapping[str, Any]) -> None:
    """Raise a typed error when payload uses Loxone's `LL` error envelope."""
    ll = payload.get("LL")
    if not isinstance(ll, Mapping):
        return

    raw_code = ll.get("Code") or ll.get("code")
    try:
        code = int(raw_code or 0)
    except (TypeError, ValueError):
        code = 0

    if code == 401:
        raise LoxoneAuthenticationError("Authentication failed.")
    if code and code >= 400:
        raise LoxoneConnectionError(f"Loxone command failed with code {code}.")


def _is_loxapp_structure_payload(payload: Mapping[str, Any]) -> bool:
    """Return True when payload looks like a real `LoxAPP3.json` structure."""
    return isinstance(payload.get("msInfo"), Mapping) and isinstance(
        payload.get("controls"), Mapping
    )


def _extract_software_version(payload: Mapping[str, Any]) -> str | None:
    """Return software version from known Miniserver payload keys."""
    for key in _VERSION_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        version = str(value).strip()
        if version:
            return version
    return None


def _build_discovery_label(name: str, host: str, server_model: str) -> str:
    """Return UI label for one discovery candidate."""
    stripped_name = name.strip() or host
    if server_model == DEFAULT_SERVER_MODEL:
        return f"{stripped_name} ({host})"
    if server_model.casefold() in stripped_name.casefold():
        return f"{stripped_name} ({host})"
    return f"{stripped_name} - {server_model} ({host})"


def _ensure_supported_miniserver_version(version: str | None) -> None:
    """Raise only when a parsed Miniserver version is explicitly unsupported."""
    if parse_miniserver_version(version) is None:
        return
    if is_supported_miniserver_version(version):
        return
    detected = str(version or "").strip() or "unknown"
    raise LoxoneVersionUnsupportedError(
        "Unsupported Loxone Miniserver software version "
        f"{detected}. Minimum supported version is {MIN_SUPPORTED_VERSION_TEXT}."
    )
