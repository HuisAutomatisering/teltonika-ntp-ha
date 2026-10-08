"""Small client for the RutOS REST API used by Teltonika devices.

The login and session handling follow the approach of the teltasync library by
Karl Beecken (Apache License 2.0), which powers the core "teltonika" integration.
It is reimplemented here with plain aiohttp so this custom integration does not pin
a library version that Home Assistant core also depends on. ``normalize_url`` and
``url_variants`` are based on the util module of the Home Assistant core Teltonika
integration (Apache License 2.0).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

from .const import API_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# RutOS error codes, see the Teltonika API documentation.
_AUTH_ERROR_CODES = {120, 121, 122, 123}
_SESSION_REJECTED_CODES = {120, 123}


class TeltonikaError(Exception):
    """Base error for the RutOS client."""


class TeltonikaConnectionError(TeltonikaError):
    """The device could not be reached or sent an unusable response."""


class TeltonikaAuthError(TeltonikaError):
    """The device rejected the credentials."""


class TeltonikaApiError(TeltonikaError):
    """The device answered with an API error (for example an unknown endpoint)."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Store the RutOS error code alongside the message."""
        super().__init__(message)
        self.code = code


def normalize_url(host: str) -> str:
    """Return ``scheme://host[:port]`` for user input, defaulting to HTTPS."""
    host_input = host.strip().rstrip("/")
    if host_input.startswith(("http://", "https://")):
        url = URL(host_input)
    else:
        url = URL(f"//{host_input}").with_scheme("https")
    return str(url.origin())


def url_variants(host: str) -> list[str]:
    """Return the base URLs to try: the given scheme, or HTTPS then HTTP."""
    normalized = normalize_url(host)
    if host.strip().startswith(("http://", "https://")):
        return [normalized]
    url = URL(normalized)
    return [str(url.with_scheme("https")), str(url.with_scheme("http"))]


def hostname_from_url(base_url: str) -> str:
    """Return the bare host name or IP address of a base URL."""
    return URL(base_url).host or base_url


def _clean(value: Any) -> Any:
    """Turn the "N/A" strings RutOS uses for missing values into None."""
    if value == "N/A":
        return None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _first_error(payload: dict[str, Any]) -> tuple[int | None, str]:
    """Return the code and text of the first error in an API reply."""
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        return (code if isinstance(code, int) else None, str(errors[0].get("error", "")))
    return None, "Unknown API error"


class RutOSClient:
    """Authenticated client for one Teltonika device."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
    ) -> None:
        """Initialize the client. ``base_url`` is ``scheme://host`` without /api."""
        self._session = session
        self._api = f"{base_url}/api"
        self._username = username
        self._password = password
        self._ssl = verify_ssl
        self._token: str | None = None
        self._token_valid_until = 0.0
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the device base URL."""
        return self._api.removesuffix("/api")

    async def _open(
        self, method: str, endpoint: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> tuple[int, dict[str, Any] | None]:
        """Send one request and return the HTTP status and JSON body."""
        try:
            async with self._session.request(
                method,
                f"{self._api}/{endpoint.lstrip('/')}",
                headers=headers,
                ssl=self._ssl,
                timeout=ClientTimeout(total=API_TIMEOUT),
                **kwargs,
            ) as resp:
                status = resp.status
                try:
                    payload = await resp.json(content_type=None)
                except ValueError:
                    payload = None
        except TimeoutError as err:
            raise TeltonikaConnectionError(f"Timeout talking to {self._api}") from err
        except (ClientError, OSError) as err:
            raise TeltonikaConnectionError(f"Cannot connect to {self._api}: {err}") from err
        return status, payload if isinstance(payload, dict) else None

    async def async_get_public_info(self) -> dict[str, Any]:
        """Return the device data RutOS exposes without logging in."""
        status, payload = await self._open("GET", "unauthorized/status")
        if not payload or not payload.get("success") or not isinstance(payload.get("data"), dict):
            raise TeltonikaConnectionError(
                f"Unexpected reply from unauthorized/status (HTTP {status})"
            )
        return _clean(payload["data"])

    async def async_login(self) -> None:
        """Log in and cache the session token."""
        status, payload = await self._open(
            "POST",
            "login",
            json={"username": self._username, "password": self._password},
        )
        if payload is None:
            raise TeltonikaConnectionError(f"Unexpected non-JSON login reply (HTTP {status})")

        data = payload.get("data")
        if payload.get("success") and isinstance(data, dict) and data.get("token"):
            self._token = str(data["token"])
            expires = data.get("expires")
            lifetime = float(expires) if isinstance(expires, int | float) else 299.0
            # Renew a few seconds early so a request never races the expiry.
            self._token_valid_until = time.monotonic() + max(lifetime - 10.0, 5.0)
            return

        code, message = _first_error(payload)
        if status == 401 or code in _AUTH_ERROR_CODES:
            raise TeltonikaAuthError(message or "Invalid username or password")
        raise TeltonikaConnectionError(f"Login failed: {message} (code {code})")

    async def async_logout(self) -> None:
        """End the session on the device. Errors are logged, not raised."""
        if self._token is None:
            return
        token, self._token = self._token, None
        self._token_valid_until = 0.0
        try:
            await self._open("POST", "logout", headers={"Authorization": f"Bearer {token}"})
        except TeltonikaConnectionError as err:
            _LOGGER.debug("Logout from %s failed: %s", self._api, err)

    async def _ensure_token(self) -> str:
        """Return a valid token, logging in when needed."""
        async with self._lock:
            if self._token is None or time.monotonic() >= self._token_valid_until:
                await self.async_login()
            if self._token is None:
                raise TeltonikaAuthError("No session token after login")
            return self._token

    async def async_get(self, endpoint: str) -> Any:
        """GET an authenticated endpoint and return its ``data`` field."""
        token = await self._ensure_token()
        status, payload = await self._open(
            "GET", endpoint, headers={"Authorization": f"Bearer {token}"}
        )

        if self._session_rejected(status, payload):
            # The device forgot our session (reboot, timeout): log in once more.
            self._token = None
            token = await self._ensure_token()
            status, payload = await self._open(
                "GET", endpoint, headers={"Authorization": f"Bearer {token}"}
            )
            if self._session_rejected(status, payload):
                self._token = None
                raise TeltonikaAuthError("Session rejected after logging in again")

        if payload is None:
            raise TeltonikaConnectionError(f"Unexpected reply from {endpoint} (HTTP {status})")
        if not payload.get("success"):
            code, message = _first_error(payload)
            if code in _AUTH_ERROR_CODES:
                raise TeltonikaAuthError(message)
            raise TeltonikaApiError(f"{endpoint}: {message}", code)
        return _clean(payload.get("data"))

    @staticmethod
    def _session_rejected(status: int, payload: dict[str, Any] | None) -> bool:
        """Return True when the device no longer accepts our token."""
        if status == 401:
            return True
        if payload is not None and not payload.get("success"):
            code, _ = _first_error(payload)
            return code in _SESSION_REJECTED_CODES
        return False

    async def async_get_device_status(self) -> dict[str, Any]:
        """Return manufacturing, firmware and model information."""
        data = await self.async_get("system/device/status")
        if not isinstance(data, dict):
            raise TeltonikaConnectionError("system/device/status returned no data")
        return data
