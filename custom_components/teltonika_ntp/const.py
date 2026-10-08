"""Constants for the Teltonika NTP Server integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "teltonika_ntp"
MANUFACTURER: Final = "Teltonika"

# How often the RutOS API and the NTP service are polled. NTP servers rate limit
# clients that ask too often (ntpd answers with a "RATE" kiss-o'-death packet), so
# keep this well above the 8 second average interval ntpd allows by default.
SCAN_INTERVAL: Final = timedelta(seconds=60)

NTP_PORT: Final = 123
NTP_TIMEOUT: Final = 5.0
API_TIMEOUT: Final = 10.0

# Model prefixes this integration is written for. DHCP discovery ignores other
# Teltonika devices so it does not compete with the core "teltonika" integration.
SUPPORTED_MODEL_PREFIXES: Final = ("NTP",)

# Synchronization states reported by the "Synchronization" sensor.
SYNC_SYNCHRONIZED: Final = "synchronized"
SYNC_UNSYNCHRONIZED: Final = "unsynchronized"
SYNC_RATE_LIMITED: Final = "rate_limited"
SYNC_NO_RESPONSE: Final = "no_response"
SYNC_STATES: Final = [
    SYNC_SYNCHRONIZED,
    SYNC_UNSYNCHRONIZED,
    SYNC_RATE_LIMITED,
    SYNC_NO_RESPONSE,
]
