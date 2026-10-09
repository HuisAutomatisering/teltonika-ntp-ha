"""Tests for the Teltonika NTP Server config flow."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.teltonika_ntp.api import (
    TeltonikaAuthError,
    TeltonikaConnectionError,
)
from custom_components.teltonika_ntp.const import DOMAIN
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .conftest import BASE_URL, DEVICE_STATUS, HOST, MAC, PUBLIC_INFO, SERIAL

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

USER_INPUT = {
    CONF_HOST: HOST,
    CONF_USERNAME: "homeassistant",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_SSL: False,
}
CREDENTIALS = {CONF_USERNAME: "homeassistant", CONF_PASSWORD: "secret"}
DHCP_INFO = DhcpServiceInfo(ip=HOST, hostname="ntp001", macaddress="209727000001")


async def test_user_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A manual setup creates an entry keyed by serial number."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NTP001"
    assert result["data"] == {**USER_INPUT, CONF_HOST: BASE_URL}
    assert result["result"].unique_id == SERIAL
    mock_client.async_logout.assert_awaited()


async def test_user_flow_http_fallback(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """When HTTPS is not available the flow stores the HTTP address."""
    mock_client.async_get_public_info.side_effect = [
        TeltonikaConnectionError("no https"),
        PUBLIC_INFO,
    ]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == f"http://{HOST}"


@pytest.mark.parametrize(
    ("side_effect", "status", "error"),
    [
        (TeltonikaAuthError("denied"), DEVICE_STATUS, "invalid_auth"),
        (TeltonikaConnectionError("down"), DEVICE_STATUS, "cannot_connect"),
        (RuntimeError("boom"), DEVICE_STATUS, "unknown"),
        (None, {"mnfinfo": {}, "static": {}}, "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    side_effect: Exception | None,
    status: dict,
    error: str,
) -> None:
    """Errors are shown on the form and the flow recovers afterwards."""
    mock_client.async_get_device_status.side_effect = side_effect
    mock_client.async_get_device_status.return_value = status

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_client.async_get_device_status.side_effect = None
    mock_client.async_get_device_status.return_value = DEVICE_STATUS
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The same device cannot be added twice."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_dhcp_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A discovered NTP server asks for credentials and creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    assert result["description_placeholders"] == {"name": "NTP001", "host": HOST}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**CREDENTIALS, CONF_HOST: BASE_URL, CONF_VERIFY_SSL: False}
    assert result["result"].unique_id == SERIAL


async def test_dhcp_confirm_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Wrong credentials on a discovered device show an error and can be retried."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    mock_client.async_get_device_status.side_effect = TeltonikaAuthError("denied")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.async_get_device_status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_dhcp_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A device that does not answer is not offered."""
    mock_client.async_get_public_info.side_effect = TeltonikaConnectionError("down")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_dhcp_not_supported(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Teltonika routers are left to the core integration."""
    mock_client.async_get_public_info.return_value = {
        **PUBLIC_INFO,
        "device_name": "RUTX11",
        "device_model": "RUTX11",
    }
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.parametrize(
    ("stored_host", "expected_host"),
    [("https://192.0.2.20", BASE_URL), (BASE_URL, BASE_URL)],
)
async def test_dhcp_already_configured(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    stored_host: str,
    expected_host: str,
) -> None:
    """A known device is matched by MAC address and its new address is stored."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_HOST: stored_host}
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, SERIAL)},
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == expected_host


async def test_dhcp_ignores_other_domains(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A device with the same MAC in another integration does not block discovery."""
    other = MockConfigEntry(domain="teltonika", unique_id=SERIAL)
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={("teltonika", SERIAL)},
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=DHCP_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"


async def test_reauth_flow(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """New credentials replace the stored ones."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    new_credentials = {CONF_USERNAME: "homeassistant", CONF_PASSWORD: "new-secret"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_credentials)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new-secret"


async def test_reauth_flow_error(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected password shows an error and can be retried."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)

    mock_client.async_get_device_status.side_effect = TeltonikaAuthError("denied")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.async_get_device_status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_wrong_device(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Credentials for another device are refused."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_get_device_status.return_value = {
        **DEVICE_STATUS,
        "mnfinfo": {**DEVICE_STATUS["mnfinfo"], "serial": "9999999999"},
    }
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_reconfigure_flow(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The address and credentials can be changed."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_input = {**USER_INPUT, CONF_HOST: "192.0.2.30"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_input)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "https://192.0.2.30"


async def test_reconfigure_flow_error(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable address shows an error and can be corrected."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)

    mock_client.async_get_public_info.side_effect = TeltonikaConnectionError("down")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.async_get_public_info.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_wrong_device(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Pointing an entry at another device is refused."""
    mock_config_entry.add_to_hass(hass)
    mock_client.async_get_device_status.return_value = {
        **DEVICE_STATUS,
        "mnfinfo": {**DEVICE_STATUS["mnfinfo"], "serial": "9999999999"},
    }
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_logout_always_called(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The API session is closed even when validation fails."""
    mock_client.async_get_device_status.side_effect = TeltonikaConnectionError("down")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert mock_client.async_logout.await_count == 2  # HTTPS and HTTP attempt
