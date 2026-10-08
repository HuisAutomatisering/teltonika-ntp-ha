"""Minimal asynchronous NTP (SNTPv4, RFC 4330) client.

Sends a single client-mode packet to the server and parses the reply. That is all
the integration needs to tell whether the server is synchronized and how far its
clock sits from the Home Assistant host, and a single packet per poll keeps us far
below the rate limits NTP servers enforce.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import struct
import time

from .const import NTP_PORT, NTP_TIMEOUT

# Seconds between the NTP epoch (1900-01-01) and the Unix epoch (1970-01-01).
_NTP_EPOCH_OFFSET = 2_208_988_800
_PACKET = struct.Struct("!BBbbII4sQQQQ")

LEAP_ALARM = 3  # leap indicator 3: clock not synchronized
STRATUM_KISS = 0  # stratum 0 in a reply: kiss-o'-death packet
STRATUM_UNSYNCHRONIZED = 16

MODE_CLIENT = 3
MODE_SERVER = 4
VERSION = 4


class NtpError(Exception):
    """Base error for the NTP client."""


class NtpTimeoutError(NtpError):
    """The server did not answer in time."""


class NtpProtocolError(NtpError):
    """The server sent something that is not a valid reply to our request."""


@dataclass(frozen=True, slots=True)
class NtpResult:
    """Parsed reply from an NTP server."""

    leap: int
    version: int
    stratum: int
    poll: int
    precision: int
    root_delay: float  # seconds
    root_dispersion: float  # seconds
    reference_id: str
    kiss_code: str | None
    offset: float | None  # seconds, server clock minus local clock
    delay: float | None  # seconds, round trip

    @property
    def is_kiss_of_death(self) -> bool:
        """Return True when the server refused to serve us (for example RATE)."""
        return self.kiss_code is not None

    @property
    def is_synchronized(self) -> bool:
        """Return True when the server claims a valid, synchronized time."""
        return (
            not self.is_kiss_of_death
            and self.leap != LEAP_ALARM
            and 0 < self.stratum < STRATUM_UNSYNCHRONIZED
        )


def _to_ntp(timestamp: float) -> int:
    """Convert a Unix timestamp to a 64-bit NTP timestamp."""
    seconds = timestamp + _NTP_EPOCH_OFFSET
    return int(seconds * 2**32) & 0xFFFFFFFFFFFFFFFF


def _from_ntp(value: int) -> float:
    """Convert a 64-bit NTP timestamp to a Unix timestamp."""
    return value / 2**32 - _NTP_EPOCH_OFFSET


def _short_to_seconds(value: int) -> float:
    """Convert an NTP 16.16 fixed point value to seconds."""
    return value / 2**16


def build_request(transmit: float) -> bytes:
    """Build a client-mode request with the given transmit time."""
    first = (0 << 6) | (VERSION << 3) | MODE_CLIENT
    return _PACKET.pack(first, 0, 0, 0, 0, 0, b"\x00" * 4, 0, 0, 0, _to_ntp(transmit))


def _reference_id(raw: bytes, stratum: int) -> str:
    """Decode the reference ID field.

    Stratum 0 and 1 use four ASCII characters (a kiss code or a source such as
    "GPS"); higher strata carry the IPv4 address of the upstream server.
    """
    if stratum <= 1:
        return raw.decode("ascii", errors="replace").strip("\x00 ")
    return str(ipaddress.IPv4Address(raw))


def parse_response(data: bytes, request_transmit: int, received: float) -> NtpResult:
    """Parse a server reply.

    ``request_transmit`` is the raw NTP transmit timestamp we sent, which the
    server must echo back as the origin timestamp. ``received`` is the local Unix
    time at which the reply arrived.
    """
    if len(data) < _PACKET.size:
        raise NtpProtocolError(f"Reply too short ({len(data)} bytes)")

    (
        first,
        stratum,
        poll,
        precision,
        root_delay,
        root_dispersion,
        ref_id,
        _reference,
        origin,
        receive,
        transmit,
    ) = _PACKET.unpack_from(data)

    leap = first >> 6
    version = (first >> 3) & 0x07
    mode = first & 0x07
    if mode != MODE_SERVER:
        raise NtpProtocolError(f"Unexpected mode {mode} in reply")
    if origin != request_transmit:
        raise NtpProtocolError("Reply does not match our request")

    reference_id = _reference_id(ref_id, stratum)
    kiss_code = reference_id if stratum == STRATUM_KISS else None

    offset: float | None = None
    delay: float | None = None
    if kiss_code is None and transmit:
        t1 = _from_ntp(request_transmit)
        t2 = _from_ntp(receive)
        t3 = _from_ntp(transmit)
        t4 = received
        offset = ((t2 - t1) + (t3 - t4)) / 2
        delay = (t4 - t1) - (t3 - t2)

    return NtpResult(
        leap=leap,
        version=version,
        stratum=stratum,
        poll=poll,
        precision=precision,
        root_delay=_short_to_seconds(root_delay),
        root_dispersion=_short_to_seconds(root_dispersion),
        reference_id=reference_id,
        kiss_code=kiss_code,
        offset=offset,
        delay=delay,
    )


class _NtpProtocol(asyncio.DatagramProtocol):
    """Datagram protocol that resolves a future with the first reply."""

    def __init__(self, reply: asyncio.Future[tuple[bytes, float]]) -> None:
        self._reply = reply

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self._reply.done():
            self._reply.set_result((data, time.time()))

    def error_received(self, exc: Exception) -> None:
        if not self._reply.done():
            self._reply.set_exception(NtpError(f"Network error: {exc}"))


async def async_query(host: str, port: int = NTP_PORT, timeout: float = NTP_TIMEOUT) -> NtpResult:
    """Send one request to ``host`` and return the parsed reply."""
    loop = asyncio.get_running_loop()
    reply: asyncio.Future[tuple[bytes, float]] = loop.create_future()

    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _NtpProtocol(reply), remote_addr=(host, port)
        )
    except OSError as err:
        raise NtpError(f"Cannot reach {host}:{port}: {err}") from err

    try:
        sent = time.time()
        request = build_request(sent)
        transport.sendto(request)
        try:
            async with asyncio.timeout(timeout):
                data, received = await reply
        except TimeoutError as err:
            raise NtpTimeoutError(f"No reply from {host}:{port} within {timeout} s") from err
    finally:
        transport.close()

    request_transmit = _PACKET.unpack(request)[-1]
    return parse_response(data, request_transmit, received)
