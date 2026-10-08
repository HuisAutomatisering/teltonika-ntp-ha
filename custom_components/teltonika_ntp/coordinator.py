"""Data update coordinator for the Teltonika NTP Server integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    RutOSClient,
    TeltonikaAuthError,
    TeltonikaConnectionError,
    TeltonikaError,
    hostname_from_url,
)
from .const import (
    DOMAIN,
    MANUFACTURER,
    SCAN_INTERVAL,
    SYNC_NO_RESPONSE,
    SYNC_RATE_LIMITED,
    SYNC_SYNCHRONIZED,
    SYNC_UNSYNCHRONIZED,
)
from .ntp import NtpError, NtpResult, async_query

if TYPE_CHECKING:
    from . import TeltonikaNtpConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TeltonikaNtpData:
    """Snapshot of everything the entities show."""

    ntp: NtpResult | None
    ntp_error: str | None

    @property
    def sync_state(self) -> str:
        """Return the synchronization state for the enum sensor."""
        if self.ntp is None:
            return SYNC_NO_RESPONSE
        if self.ntp.kiss_code == "RATE":
            return SYNC_RATE_LIMITED
        if self.ntp.is_synchronized:
            return SYNC_SYNCHRONIZED
        return SYNC_UNSYNCHRONIZED


class TeltonikaNtpCoordinator(DataUpdateCoordinator[TeltonikaNtpData]):
    """Poll the device's NTP service and RutOS API."""

    config_entry: TeltonikaNtpConfigEntry
    device_info: DeviceInfo

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: TeltonikaNtpConfigEntry,
        client: RutOSClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.ntp_host = hostname_from_url(client.base_url)
        self.device_status: dict[str, Any] = {}

    async def _async_setup(self) -> None:
        """Read the device identity once, before the first poll."""
        try:
            self.device_status = await self.client.async_get_device_status()
        except TeltonikaAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except TeltonikaError as err:
            raise ConfigEntryNotReady(f"Cannot read device status: {err}") from err

        mnf = self.device_status.get("mnfinfo") or {}
        static = self.device_status.get("static") or {}
        serial = mnf.get("serial") or self.config_entry.unique_id or self.config_entry.entry_id
        macs = {mac for mac in (mnf.get("mac"), mnf.get("macEth")) if mac}

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, str(serial))},
            connections={(CONNECTION_NETWORK_MAC, _format_mac(mac)) for mac in macs},
            manufacturer=MANUFACTURER,
            model=static.get("model") or static.get("device_name"),
            model_id=mnf.get("name"),
            name=static.get("device_name") or self.config_entry.title,
            hw_version=mnf.get("hwver"),
            sw_version=static.get("fw_version"),
            serial_number=str(serial),
            configuration_url=self.client.base_url,
        )

    async def _async_update_data(self) -> TeltonikaNtpData:
        """Query the NTP service."""
        ntp: NtpResult | None = None
        ntp_error: str | None = None
        try:
            ntp = await async_query(self.ntp_host)
        except NtpError as err:
            ntp_error = str(err)
            _LOGGER.debug("NTP query to %s failed: %s", self.ntp_host, err)

        if ntp is not None and ntp.is_kiss_of_death:
            _LOGGER.debug("NTP server %s answered with kiss code %s", self.ntp_host, ntp.kiss_code)

        if ntp is None:
            # Without an NTP reply, check whether the device itself is still up so
            # the entities can tell "service down" apart from "device gone".
            try:
                await self.client.async_get_public_info()
            except TeltonikaConnectionError as err:
                raise UpdateFailed(f"Device {self.ntp_host} is unreachable: {err}") from err

        return TeltonikaNtpData(ntp=ntp, ntp_error=ntp_error)


def _format_mac(mac: str) -> str:
    """Format a MAC as aa:bb:cc:dd:ee:ff, whatever separators RutOS used."""
    digits = "".join(char for char in mac if char.isalnum()).lower()
    if len(digits) != 12:
        return mac.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))
