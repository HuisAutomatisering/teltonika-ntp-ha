# Security Policy

## Supported versions

Only the latest release receives fixes.

| Version | Supported |
| ------- | --------- |
| 1.1.x   | Yes       |

## Reporting a vulnerability

Please report security issues privately through GitHub:
**Security** tab of this repository → **Report a vulnerability**.
Do not open a public issue for security problems.

You can expect a first reply within a week.

## Deployment notes

- The integration logs in to the RutOS WebUI API with the credentials you enter. They
  are stored in the Home Assistant config entry. Create a dedicated user with read-only
  rights for Home Assistant instead of using `admin`.
- Teltonika devices ship with a self-signed certificate, so certificate verification is
  off by default. Turn it on if you installed a trusted certificate on the device.
- NTP itself (UDP port 123) is unauthenticated unless NTP authentication is enabled on
  the device. Keep the NTP server on a trusted network segment.
- Diagnostics downloads redact the username, password, serial number and MAC addresses.
