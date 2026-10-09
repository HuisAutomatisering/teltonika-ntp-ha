"""Tests for setting up and unloading the Teltonika NTP Server integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.teltonika_ntp.api import (
    TeltonikaAuthError,
    TeltonikaConnectionError,
)
from custom_components.teltonika_ntp.const import DOMAIN
from custom_components.teltonika_ntp.ntp import NtpResult, NtpTimeoutError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import SERIAL


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ntp: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The entry loads, creates the device and entities, and unloads cleanly."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.model == "NTP001"
    assert device.sw_version == "NTP001_R_00.01.02"
    assert (dr.CONNECTION_NETWORK_MAC, "20:97:27:00:00:01") in device.connections

    assert hass.states.get("sensor.ntp001_synchronization").state == "synchronized"
    assert hass.states.get("sensor.ntp001_stratum").state == "1"
    assert hass.states.get("sensor.ntp001_reference").state == "GPS"
    assert float(hass.states.get("sensor.ntp001_offset").state) == pytest.approx(0.2)
    assert hass.states.get("binary_sensor.ntp001_ntp_service").state == STATE_ON
    assert hass.states.get("binary_sensor.ntp001_time_sync").state == STATE_OFF

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_logout.assert_awaited()


async def test_setup_auth_failed(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ntp: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Rejected credentials stop setup and start a reauthentication flow."""
    mock_client.async_get_device_status.side_effect = TeltonikaAuthError("denied")
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_retry_when_api_unreachable(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ntp: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unreachable API postpones setup."""
    mock_client.async_get_device_status.side_effect = TeltonikaConnectionError("down")
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retry_when_device_offline(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ntp: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No NTP reply and no API reply means the device is gone: retry later."""
    mock_ntp.side_effect = NtpTimeoutError("no reply")
    mock_client.async_get_public_info.side_effect = TeltonikaConnectionError("down")
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    ("ntp_reply", "ntp_error", "sync_state", "service_state"),
    [
        (None, NtpTimeoutError("no reply"), "no_response", STATE_OFF),
        (
            NtpResult(0, 4, 0, 3, 0, 0.0, 0.0, "RATE", "RATE", None, None),
            None,
            "rate_limited",
            STATE_ON,
        ),
        (
            NtpResult(3, 4, 16, 4, -17, 0.0, 0.0, "INIT", None, 0.0, 0.004),
            None,
            "unsynchronized",
            STATE_ON,
        ),
    ],
)
async def test_sync_states(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_ntp: AsyncMock,
    mock_config_entry: MockConfigEntry,
    ntp_reply: NtpResult | None,
    ntp_error: Exception | None,
    sync_state: str,
    service_state: str,
) -> None:
    """Every way the NTP service can answer maps to the right states."""
    mock_ntp.return_value = ntp_reply
    mock_ntp.side_effect = ntp_error
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.ntp001_synchronization").state == sync_state
    assert hass.states.get("binary_sensor.ntp001_ntp_service").state == service_state
    assert hass.states.get("binary_sensor.ntp001_time_sync").state == STATE_ON
