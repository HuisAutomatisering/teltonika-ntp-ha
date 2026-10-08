"""Config flow for the Teltonika NTP Server integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .api import RutOSClient, TeltonikaAuthError, TeltonikaError, url_variants
from .const import DOMAIN, SUPPORTED_MODEL_PREFIXES

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): _PASSWORD,
        vol.Optional(CONF_VERIFY_SSL, default=False): bool,
    }
)
CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): _PASSWORD,
    }
)


class CannotConnect(Exception):
    """The device could not be reached."""


class InvalidAuth(Exception):
    """The credentials were rejected."""


def _entry_ids_for_mac(hass: HomeAssistant, mac: str) -> list[str]:
    """Return the config entry IDs of devices that have this MAC address.

    Home Assistant 2026.x replaced ``async_get_device`` and
    ``DeviceEntry.config_entries`` (a device now belongs to a single config entry).
    Use the new API when it exists and fall back for older releases.
    """
    registry = dr.async_get(hass)
    connections = {(dr.CONNECTION_NETWORK_MAC, mac)}

    get_devices = getattr(registry, "async_get_devices", None)
    if get_devices is not None:
        devices = list(get_devices(connections=connections))
    else:
        device = registry.async_get_device(connections=connections)
        devices = [device] if device is not None else []

    entry_ids: list[str] = []
    for device in devices:
        entry_id = getattr(device, "config_entry_id", None)
        if entry_id is not None:
            entry_ids.append(entry_id)
        else:
            entry_ids.extend(device.config_entries)
    return entry_ids


def _is_supported(model: str | None) -> bool:
    """Return True for models this integration is written for."""
    return bool(model) and str(model).upper().startswith(SUPPORTED_MODEL_PREFIXES)


async def _async_validate(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Log in and read the identity of the device.

    Tries HTTPS first and falls back to HTTP when no scheme was given.
    """
    session = async_get_clientsession(hass)
    last_error: Exception | None = None

    for base_url in url_variants(data[CONF_HOST]):
        client = RutOSClient(
            session,
            base_url,
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            verify_ssl=data.get(CONF_VERIFY_SSL, False),
        )
        try:
            await client.async_get_public_info()
            status = await client.async_get_device_status()
        except TeltonikaAuthError as err:
            raise InvalidAuth from err
        except TeltonikaError as err:
            _LOGGER.debug("Cannot use %s: %s", base_url, err)
            last_error = err
            continue
        finally:
            await client.async_logout()

        mnf = status.get("mnfinfo") or {}
        static = status.get("static") or {}
        serial = mnf.get("serial")
        if not serial:
            raise CannotConnect("Device did not report a serial number")
        return {
            "host": base_url,
            "serial": str(serial),
            "title": static.get("device_name") or static.get("model") or "Teltonika NTP",
            "model": static.get("model") or mnf.get("name") or "",
        }

    raise CannotConnect from last_error


class TeltonikaNtpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Teltonika NTP server."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_host: str | None = None

    async def _async_try(
        self, data: Mapping[str, Any], errors: dict[str, str]
    ) -> dict[str, str] | None:
        """Validate input and fill ``errors``; return device info on success."""
        try:
            return await _async_validate(self.hass, data)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected error while validating the device")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info = await self._async_try(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(info["serial"])
                self._abort_if_unique_id_configured(updates={CONF_HOST: info["host"]})
                return self.async_create_entry(
                    title=info["title"], data={**user_input, CONF_HOST: info["host"]}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle a Teltonika device found through DHCP."""
        host = discovery_info.ip
        session = async_get_clientsession(self.hass)

        public: dict[str, Any] | None = None
        for base_url in url_variants(host):
            try:
                public = await RutOSClient(session, base_url, "", "").async_get_public_info()
                break
            except TeltonikaError:
                continue
        if public is None:
            return self.async_abort(reason="cannot_connect")

        # The core "teltonika" integration handles routers; only take NTP servers.
        if not _is_supported(public.get("device_model") or public.get("device_name")):
            return self.async_abort(reason="not_supported")

        # Entries are keyed by serial number, which needs a login to read. Match a
        # device that is already set up by its MAC address instead.
        mac = dr.format_mac(discovery_info.macaddress)
        for entry_id in _entry_ids_for_mac(self.hass, mac):
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            if entry.data.get(CONF_HOST) != base_url:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: base_url}
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()

        self._discovered_host = host
        name = public.get("device_name") or public.get("deviceName") or "Teltonika NTP"
        self.context["title_placeholders"] = {"name": str(name), "host": host}
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials for a discovered device."""
        errors: dict[str, str] = {}
        if user_input is not None and self._discovered_host:
            data = {**user_input, CONF_HOST: self._discovered_host, CONF_VERIFY_SSL: False}
            info = await self._async_try(data, errors)
            if info is not None:
                await self.async_set_unique_id(info["serial"], raise_on_progress=False)
                self._abort_if_unique_id_configured(updates={CONF_HOST: info["host"]})
                return self.async_create_entry(
                    title=info["title"], data={**data, CONF_HOST: info["host"]}
                )

        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
            description_placeholders=self.context.get("title_placeholders", {}),
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, **user_input}
            info = await self._async_try(data, errors)
            if info is not None:
                await self.async_set_unique_id(info["serial"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, {CONF_USERNAME: entry.data.get(CONF_USERNAME)}
            ),
            errors=errors,
            description_placeholders={"name": entry.title, "host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or credentials of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            info = await self._async_try(user_input, errors)
            if info is not None:
                await self.async_set_unique_id(info["serial"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={**user_input, CONF_HOST: info["host"]}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or {**entry.data, CONF_PASSWORD: ""}
            ),
            errors=errors,
        )
