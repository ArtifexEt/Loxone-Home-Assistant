"""Authenticated WebRTC signaling for Loxone Intercom Gen 2."""

from __future__ import annotations

import asyncio
import base64
import re
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from cryptography import x509
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if TYPE_CHECKING:
    from aiohttp import ClientSession, ClientWebSocketResponse, Fingerprint

    from .bridge import LoxoneBridge

_WEBSOCKET_PROTOCOL = "webrtc-signaling"
_LOCAL_REACH_MODE = 2
_REQUEST_TIMEOUT = 10
_CONNECTION_TIMEOUT = 15
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){2}(?:[0-9a-f]{16}|[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_PHOTO_PATH = re.compile(r"[A-Za-z0-9._~+=/-]+")
_MAX_PHOTO_BYTES = 4 * 1024 * 1024


class IntercomSignalingError(Exception):
    """An Intercom signaling operation failed."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def intercom_websocket_url(bridge: LoxoneBridge, device_uuid: str) -> str:
    """Resolve a device proxy on the configured Miniserver origin."""
    if getattr(bridge, "_remote_serial", None):
        raise IntercomSignalingError("Intercom signaling requires a local Miniserver connection.")
    if not isinstance(device_uuid, str) or not _UUID.fullmatch(device_uuid):
        raise IntercomSignalingError("The Intercom device UUID is invalid or missing.")

    proxy_path = f"/proxy/{device_uuid}/"
    url = bridge.resolve_http_url(proxy_path)
    if not isinstance(url, str):
        raise IntercomSignalingError("The Miniserver did not provide an Intercom proxy URL.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise IntercomSignalingError("The Intercom proxy URL is invalid.") from None

    expected_host = str(bridge.host).strip("[]").casefold()
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or (443 if port is None else port) != bridge.port
        or parsed.username is not None
        or not parsed.path.endswith(proxy_path)
        or parsed.query
        or parsed.fragment
    ):
        raise IntercomSignalingError("Intercom signaling requires the Miniserver's HTTPS origin.")
    return urlunsplit(("wss", parsed.netloc, parsed.path, "", ""))


class IntercomSignaling:
    """One authenticated Intercom WebSocket and its in-flight JSON-RPC requests."""

    def __init__(
        self,
        client: ClientSession,
        websocket: ClientWebSocketResponse,
        proxy_url: str,
        tls: bool | Fingerprint,
    ) -> None:
        self._client = client
        self._websocket = websocket
        self._proxy_url = proxy_url
        self._tls = tls
        self._photo_token: str | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    async def async_connect(cls, bridge: LoxoneBridge, device_uuid: str) -> IntercomSignaling:
        """Open the Miniserver proxy and answer the Intercom's authentication challenge."""
        from aiohttp import ClientError, ClientSession, Fingerprint, TraceConfig

        url = intercom_websocket_url(bridge, device_uuid)
        token = bridge.token
        if not isinstance(token, str) or not token:
            raise IntercomSignalingError("No authenticated Miniserver token is available.")

        tls: bool | Fingerprint = True
        if not bridge.verify_ssl:
            fingerprint = bridge.miniserver_tls_fingerprint()
            if fingerprint is None:
                raise IntercomSignalingError("The authenticated Miniserver TLS certificate is unavailable.")
            tls = Fingerprint(fingerprint)

        async def reject_redirect(*_args: Any) -> None:
            raise IntercomSignalingError("The Intercom proxy redirected the connection.")

        trace = TraceConfig()
        trace.on_request_redirect.append(reject_redirect)
        client = ClientSession(trace_configs=[trace])
        try:
            async with asyncio.timeout(_CONNECTION_TIMEOUT):
                websocket = await client.ws_connect(
                    url,
                    protocols=(_WEBSOCKET_PROTOCOL,),
                    ssl=tls,
                )
                if websocket.protocol != _WEBSOCKET_PROTOCOL:
                    raise IntercomSignalingError("The Intercom refused its signaling subprotocol.")
                connection = cls(client, websocket, url, tls)
                await connection._async_authenticate(bridge.username, token)
        except (Exception, asyncio.CancelledError) as error:
            await client.close()
            if isinstance(error, (ClientError, TimeoutError, ValueError, OSError)):
                raise IntercomSignalingError("Could not authenticate the Intercom signaling connection.") from None
            raise

        connection._reader_task = asyncio.create_task(connection._async_read(), name="loxone_intercom_signaling")
        return connection

    async def _async_authenticate(self, username: str, token: str) -> None:
        authenticated = False
        while True:
            message = await self._async_receive()
            method = message.get("method")
            if method == "authenticate" and "id" in message and not authenticated:
                params = message.get("params")
                response = _authentication_response(params, username, token)
                await self._async_respond(message["id"], response)
                self._photo_token = params[0]
                authenticated = True
            elif method == "ready" and authenticated:
                return
            elif method == "ready" or message.get("error") or method == "kick":
                raise IntercomSignalingError("The Intercom did not accept authentication.")
            elif method == "reachMode" and "id" in message:
                await self._async_respond(message["id"], [_LOCAL_REACH_MODE])

    async def async_request(self, method: str, params: list[Any] | None = None) -> Any:
        """Send a JSON-RPC request and return its result payload."""
        if self._closed:
            raise IntercomSignalingError("The Intercom signaling connection is closed.")
        request_id = self._next_id
        self._next_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params

        response = self._pending[request_id] = asyncio.get_running_loop().create_future()
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                await self._websocket.send_json(request)
                return await response
        except TimeoutError:
            raise IntercomSignalingError("The Intercom did not answer a signaling request.") from None
        except (OSError, RuntimeError):
            raise IntercomSignalingError("The Intercom signaling request could not be sent.") from None
        finally:
            self._pending.pop(request_id, None)
            if not response.done():
                response.cancel()
            elif not response.cancelled():
                response.exception()

    async def async_call(
        self,
        sdp: str,
        *,
        action: str = "new",
    ) -> str:
        """Send a WebRTC offer for a new call or renegotiate its audio tracks."""
        if action not in {"new", "add_audio", "remove_audio"}:
            raise ValueError("Unsupported Intercom call action.")
        params = [{"type": "offer", "sdp": sdp}, action, False]
        if action != "new":
            # The audio renegotiation uses the Loxone offer extension for live video.
            params.append(0)
        result = await self.async_request(
            "call",
            params,
        )
        answer = result.get("sdp") if isinstance(result, dict) else None
        if not isinstance(answer, str) or not answer:
            raise IntercomSignalingError("The Intercom returned no WebRTC answer.")
        return answer

    async def async_hangup(self) -> None:
        """End the WebRTC call belonging to this authenticated session."""
        await self.async_request("hangup")

    async def async_get_archive_photo(self, image_path: str) -> bytes | None:
        """Download a private archive JPEG over the authenticated proxy origin."""
        from aiohttp import ClientError
        from yarl import URL

        if self._closed or self._photo_token is None:
            return None
        url = _archive_photo_url(self._proxy_url, image_path, self._photo_token)
        if url is None:
            return None

        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT), self._client.get(
                URL(url, encoded=True), ssl=self._tls, allow_redirects=False
            ) as response:
                if (
                    response.status != 200
                    or response.content_type not in {"image/jpeg", "image/jpg", "application/octet-stream"}
                    or (response.content_length or 0) > _MAX_PHOTO_BYTES
                ):
                    return None
                photo = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if len(photo) + len(chunk) > _MAX_PHOTO_BYTES:
                        return None
                    photo.extend(chunk)
                return bytes(photo) if photo.startswith(b"\xff\xd8") else None
        except (ClientError, OSError, RuntimeError, TimeoutError, ValueError):
            return None

    async def async_add_ice_candidate(
        self, candidate: str, sdp_mline_index: int, sdp_mid: str | None = None
    ) -> None:
        """Forward a local ICE candidate to the Intercom."""
        params: list[Any] = [candidate, sdp_mline_index]
        if sdp_mid is not None:
            params.append(sdp_mid)
        await self.async_request("addIceCandidate", params)

    async def async_next_notification(self) -> dict[str, Any]:
        """Read the next unsolicited notification, including remote ICE candidates."""
        message = await self._notifications.get()
        if message is None:
            self._notifications.put_nowait(None)
            raise IntercomSignalingError("The Intercom signaling connection is closed.")
        return message

    async def async_close(self) -> None:
        """Release the signaling socket and fail any pending request."""
        task = self._reader_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._client.close()
        self._mark_closed()

    async def _async_receive(self) -> dict[str, Any]:
        try:
            message = await self._websocket.receive_json()
        except Exception:
            raise IntercomSignalingError("The Intercom signaling socket stopped receiving.") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise IntercomSignalingError("The Intercom sent an invalid signaling message.")
        return message

    async def _async_respond(self, request_id: Any, data: list[Any] | None = None) -> None:
        result: dict[str, Any] = {"code": 200, "message": "Ok"}
        if data is not None:
            result["data"] = data
        await self._websocket.send_json({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _async_read(self) -> None:
        try:
            while not self._websocket.closed:
                message = await self._async_receive()
                request_id = message.get("id")
                method = message.get("method")
                if isinstance(method, str):
                    await self._async_dispatch_notification(message, method, request_id)
                elif isinstance(request_id, int):
                    self._handle_response(request_id, message)
        except (IntercomSignalingError, asyncio.QueueFull, OSError, RuntimeError):
            pass
        finally:
            self._mark_closed()
            await self._client.close()

    async def _async_dispatch_notification(
        self, message: dict[str, Any], method: str, request_id: Any
    ) -> None:
        if request_id is not None:
            if method == "reachMode":
                await self._async_respond(request_id, [_LOCAL_REACH_MODE])
                return
            if method == "addIceCandidate":
                await self._async_respond(request_id)
            else:
                await self._websocket.send_json({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                })
                return
        self._notifications.put_nowait(message)

    def _handle_response(self, request_id: int, message: dict[str, Any]) -> None:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return

        result = message.get("result")
        error = message.get("error")
        status = error if isinstance(error, dict) else result
        code = status.get("code") if isinstance(status, dict) else None
        if error is not None or (isinstance(code, int) and not 200 <= code < 300):
            future.set_exception(IntercomSignalingError(
                "The Intercom rejected a signaling request.", code=code if isinstance(code, int) else None
            ))
        else:
            future.set_result(result.get("data") if isinstance(result, dict) else result)

    def _mark_closed(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._photo_token = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(IntercomSignalingError("The Intercom signaling connection was closed."))
        if self._notifications.full():
            self._notifications.get_nowait()
        self._notifications.put_nowait(None)


def _archive_photo_url(proxy_url: str, image_path: str, token: str) -> str | None:
    if len(image_path) > 4096 or any(ord(char) <= 32 for char in image_path):
        return None
    try:
        photo = urlsplit(image_path)
    except ValueError:
        return None

    path = photo.path.removeprefix("/")
    lower_path = path.lower()
    if (
        photo.scheme
        or photo.netloc
        or photo.fragment
        or image_path.startswith("//")
        or not _PHOTO_PATH.fullmatch(path)
        or not (
            lower_path.startswith(("snapshots/", "jpg/"))
            or lower_path.endswith((".jpg", ".jpeg"))
        )
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return None
    query = [
        pair for pair in photo.query.split("&")
        if pair and unquote_plus(pair.partition("=")[0]).casefold() != "autht"
    ]
    query.append(urlencode({"autht": token}))
    proxy = urlsplit(proxy_url)
    return urlunsplit(("https", proxy.netloc, proxy.path + path, "&".join(query), ""))


def _authentication_response(params: Any, username: str, token: str) -> list[str]:
    """Answer the Intercom challenge using the existing Miniserver login token."""
    if (
        not isinstance(params, list)
        or not params
        or not isinstance(params[0], str)
        or not 1 <= len(params[0]) <= 256
    ):
        raise IntercomSignalingError("The Intercom authentication challenge is invalid.")

    try:
        public_key = _challenge_public_key(params)
        key = secrets.token_bytes(32)
        iv = secrets.token_bytes(16)
        secret = f"{key.hex()}:{iv.hex()}:{params[0]}".encode()
        wrapped_secret = public_key.encrypt(secret, rsa_padding.PKCS1v15())
        padder = padding.PKCS7(128).padder()
        token_bytes = padder.update(token.encode()) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted_token = encryptor.update(token_bytes) + encryptor.finalize()
    except (TypeError, ValueError, IndexError):
        raise IntercomSignalingError("The Intercom authentication challenge is invalid.") from None

    return [username, base64.b64encode(wrapped_secret).decode(), base64.b64encode(encrypted_token).decode()]


def _challenge_public_key(params: list[Any]) -> RSAPublicKey:
    key: Any
    if len(params) > 3 and isinstance(params[3], str) and params[3]:
        pem = params[3].encode()
        if pem.startswith(b"-----BEGIN CERTIFICATE-----"):
            key = x509.load_pem_x509_certificate(pem).public_key()
        else:
            key = serialization.load_pem_public_key(pem)
    elif len(params) >= 3 and isinstance(params[1], str) and isinstance(params[2], str):
        if len(params[1]) > 2048 or len(params[2]) > 16:
            raise ValueError("Invalid Intercom public keys.")
        key = RSAPublicNumbers(int(params[2], 16), int(params[1], 16)).public_key()
    else:
        raise ValueError("No Intercom public key.")

    if not isinstance(key, RSAPublicKey) or not 2048 <= key.key_size <= 8192:
        raise ValueError("Unsupported Intercom public key.")
    return key
