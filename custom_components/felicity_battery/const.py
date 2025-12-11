from __future__ import annotations
# -*- coding: utf-8 -*-

from homeassistant.const import Platform

DOMAIN = "felicity_battery"

DEFAULT_PORT = 53970
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Config entry option: what kind of Felicity device this entry represents.
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_BATTERY = "battery"
DEVICE_TYPE_INVERTER = "inverter"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]
