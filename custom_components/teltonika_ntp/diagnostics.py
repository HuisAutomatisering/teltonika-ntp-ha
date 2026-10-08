"""Diagnostics support for the Teltonika NTP Server integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import TeltonikaNtpConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "serial", "mac", "macEth", "batch"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TeltonikaNtpConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "device_status": async_redact_data(coordinator.device_status, TO_REDACT),
        "ntp_host": coordinator.ntp_host,
        "sync_state": data.sync_state if data else None,
        "ntp": asdict(data.ntp) if data and data.ntp else None,
        "ntp_error": data.ntp_error if data else None,
    }
