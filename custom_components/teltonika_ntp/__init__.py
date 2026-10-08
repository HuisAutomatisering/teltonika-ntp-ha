"""The Teltonika NTP Server integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RutOSClient
from .coordinator import TeltonikaNtpCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type TeltonikaNtpConfigEntry = ConfigEntry[TeltonikaNtpCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TeltonikaNtpConfigEntry) -> bool:
    """Set up a Teltonika NTP server from a config entry."""
    client = RutOSClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
    )
    coordinator = TeltonikaNtpCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TeltonikaNtpConfigEntry) -> bool:
    """Unload a config entry and close the API session on the device."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.async_logout()
    return unload_ok
