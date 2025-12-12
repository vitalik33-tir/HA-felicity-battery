from __future__ import annotations
# -*- coding: utf-8 -*-

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN


def _get(data: Any, path: tuple[Any, ...]) -> Any:
    cur: Any = data
    try:
        for p in path:
            if isinstance(cur, dict) and isinstance(p, str):
                cur = cur.get(p)
            else:
                cur = cur[p]
        return cur
    except (KeyError, IndexError, TypeError):
        return None


@dataclass
class FelicityInverterBinarySensorDescription(BinarySensorEntityDescription):
    """Extended description for Felicity inverter binary sensors."""

    is_on_fn: Callable[[dict[str, Any]], bool | None] = lambda _data: None


INVERTER_BINARY_SENSOR_DESCRIPTIONS: tuple[
    FelicityInverterBinarySensorDescription, ...
] = (
    FelicityInverterBinarySensorDescription(
        key="inv_fault_active",
        name="Inverter Fault Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: (d.get("fault") or 0) != 0 or (d.get("Bfault") or 0) != 0,
    ),
    FelicityInverterBinarySensorDescription(
        key="inv_warning_active",
        name="Inverter Warning Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: (d.get("warn") or 0) != 0 or (d.get("Bwarn") or 0) != 0,
    ),
    FelicityInverterBinarySensorDescription(
        key="grid_input_present",
        name="Grid Input Present",
        device_class=BinarySensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: (_get(d, ("ACin", 0, 0)) or 0) >= 800,  # >= 80.0V
    ),
    FelicityInverterBinarySensorDescription(
        key="ac_output_active",
        name="AC Output Active",
        device_class=BinarySensorDeviceClass.POWER,
        is_on_fn=lambda d: (_get(d, ("ACout", 2, 0)) or 0) >= 50,  # >= 5.0W
    ),
)


def create_inverter_binary_sensors(
    coordinator: DataUpdateCoordinator,
    entry: ConfigEntry,
) -> list["FelicityInverterBinarySensor"]:
    return [
        FelicityInverterBinarySensor(coordinator, entry, desc)
        for desc in INVERTER_BINARY_SENSOR_DESCRIPTIONS
    ]


class FelicityInverterBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Inverter binary sensors."""

    _attr_has_entity_name = True
    entity_description: FelicityInverterBinarySensorDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        description: FelicityInverterBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def device_info(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        serial = data.get("DevSN") or data.get("wifiSN") or self._entry.entry_id

        basic = data.get("_basic") or {}
        sw_version = basic.get("version")

        model = "Inverter"
        if data.get("Type") is not None and data.get("SubType") is not None:
            model = f"Type {data.get('Type')}/{data.get('SubType')}"

        host = self._entry.data.get(CONF_HOST)

        info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{serial}_inverter")},
            "name": self._entry.data.get("name", "Felicity Inverter"),
            "manufacturer": "Felicity",
            "model": model,
            "sw_version": sw_version,
            "serial_number": serial,
        }

        if host:
            info["configuration_url"] = f"http://{host}"
            info["ip_address"] = host

        return info

    @property
    def is_on(self) -> bool | None:
        data: dict[str, Any] = self.coordinator.data or {}
        try:
            return self.entity_description.is_on_fn(data)
        except Exception:
            return None
