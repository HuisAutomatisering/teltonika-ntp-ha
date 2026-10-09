"""Tests for the SNTP client."""

from __future__ import annotations

import struct
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.teltonika_ntp import ntp

_PACKET = struct.Struct("!BBbbII4sQQQQ")


def _reply(
    request: bytes,
    *,
    leap: int = 0,
    stratum: int = 1,
    ref: bytes = b"GPS\x00",
    shift: float = 0.0,
    kiss: bool = False,
    mode: int = 4,
) -> bytes:
    """Build a server reply to ``request``; ``shift`` moves the server clock."""
    origin = _PACKET.unpack(request)[-1]
    now = ntp._to_ntp(time.time() + shift)
    if kiss:
        return _PACKET.pack((3 << 6) | (4 << 3) | 4, 0, 3, 0, 0, 0, b"RATE", 0, origin, origin, 0)
    first = (leap << 6) | (4 << 3) | mode
    return _PACKET.pack(first, stratum, 4, -20, 0, 76, ref, now, origin, now, now)


def _parse(reply: bytes, request: bytes) -> ntp.NtpResult:
    return ntp.parse_response(reply, _PACKET.unpack(request)[-1], time.time())


def test_synchronized_gps_server() -> None:
    """A stratum 1 GPS reply is synchronized and gives a sensible offset."""
    request = ntp.build_request(time.time())
    result = _parse(_reply(request, shift=0.025), request)

    assert result.is_synchronized
    assert result.stratum == 1
    assert result.reference_id == "GPS"
    assert result.kiss_code is None
    assert result.offset == pytest.approx(0.025, abs=0.005)
    assert result.root_dispersion == pytest.approx(76 / 2**16)


def test_kiss_of_death_rate() -> None:
    """A RATE kiss-o'-death packet, as the NTP001 sends it, is recognised."""
    request = ntp.build_request(time.time())
    result = _parse(_reply(request, kiss=True), request)

    assert result.is_kiss_of_death
    assert result.kiss_code == "RATE"
    assert not result.is_synchronized
    assert result.offset is None
    assert result.delay is None


def test_unsynchronized_server() -> None:
    """Leap indicator 3 and stratum 16 mean not synchronized."""
    request = ntp.build_request(time.time())
    result = _parse(_reply(request, leap=3, stratum=16, ref=b"INIT"), request)

    assert not result.is_synchronized
    assert not result.is_kiss_of_death


def test_stratum_2_reference_is_ip() -> None:
    """Above stratum 1 the reference ID is the upstream server address."""
    request = ntp.build_request(time.time())
    result = _parse(_reply(request, stratum=2, ref=bytes([192, 0, 2, 1])), request)

    assert result.reference_id == "192.0.2.1"
    assert result.is_synchronized


@pytest.mark.parametrize(
    ("reply", "origin"),
    [
        (b"\x00" * 10, None),  # too short
        (None, 12345),  # does not answer our request
    ],
)
def test_invalid_replies(reply: bytes | None, origin: int | None) -> None:
    """Replies that are short or do not match the request are rejected."""
    request = ntp.build_request(time.time())
    data = reply if reply is not None else _reply(request)
    with pytest.raises(ntp.NtpProtocolError):
        ntp.parse_response(data, origin or _PACKET.unpack(request)[-1], time.time())


def test_wrong_mode_rejected() -> None:
    """A reply that is not in server mode is rejected."""
    request = ntp.build_request(time.time())
    with pytest.raises(ntp.NtpProtocolError):
        _parse(_reply(request, mode=3), request)


class _FakeEndpoint:
    """Stand-in for loop.create_datagram_endpoint; never opens a real socket."""

    def __init__(self, respond: Any = None, error: OSError | None = None) -> None:
        self.respond = respond
        self.error = error
        self.transport = MagicMock()

    async def __call__(self, loop: Any, factory: Any, host: str, port: int) -> Any:
        if self.error is not None:
            raise self.error
        protocol = factory()

        def _sendto(data: bytes) -> None:
            if self.respond is not None:
                loop.call_soon(self.respond, protocol, data)

        self.transport.sendto.side_effect = _sendto
        return self.transport


async def _query_with(endpoint: _FakeEndpoint, timeout: float = 1.0) -> ntp.NtpResult:
    with patch.object(ntp, "_create_endpoint", endpoint):
        return await ntp.async_query("192.0.2.10", timeout=timeout)


async def test_async_query_success() -> None:
    """A full request/reply round trip returns the parsed reply and closes the socket."""
    endpoint = _FakeEndpoint(
        respond=lambda proto, data: proto.datagram_received(_reply(data), ("192.0.2.10", 123))
    )
    result = await _query_with(endpoint)

    assert result.is_synchronized
    endpoint.transport.close.assert_called_once()


async def test_async_query_timeout() -> None:
    """No reply raises a timeout error."""
    endpoint = _FakeEndpoint()
    with pytest.raises(ntp.NtpTimeoutError):
        await _query_with(endpoint, timeout=0.05)
    endpoint.transport.close.assert_called_once()


async def test_async_query_network_error() -> None:
    """An ICMP error reported by the transport raises an NTP error."""
    endpoint = _FakeEndpoint(
        respond=lambda proto, data: proto.error_received(OSError("unreachable"))
    )
    with pytest.raises(ntp.NtpError, match="Network error"):
        await _query_with(endpoint)


async def test_async_query_cannot_open_socket() -> None:
    """A failure to open the socket raises an NTP error."""
    with pytest.raises(ntp.NtpError, match="Cannot reach"):
        await _query_with(_FakeEndpoint(error=OSError("no route")))


async def test_async_query_ignores_second_reply() -> None:
    """Only the first datagram counts."""

    def _respond_twice(proto: Any, data: bytes) -> None:
        proto.datagram_received(_reply(data), ("192.0.2.10", 123))
        proto.datagram_received(b"garbage", ("192.0.2.10", 123))
        proto.error_received(OSError("late"))

    result = await _query_with(_FakeEndpoint(respond=_respond_twice))
    assert result.is_synchronized
