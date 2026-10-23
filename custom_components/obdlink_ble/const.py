"""Integration constants."""

from homeassistant.const import Platform

DOMAIN = "obdlink_ble"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
AWAY_SECONDS = 120
CAPTURE_SECONDS = 15
