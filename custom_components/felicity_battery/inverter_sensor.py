from __future__ import annotations
# -*- coding: utf-8 -*-

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN


def _get(data: Any, path: tuple[Any, ...]) -> Any:
    """Safe get for nested dict/list structures."""
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


def _div(value: Any, divider: float, ndigits: int | None = None) -> Any:
    if value is None:
        return None
    try:
        v = float(value) / divider
    except (TypeError, ValueError):
        return None
    return round(v, ndigits) if ndigits is not None else v


@dataclass
class FelicityInverterSensorDescription(SensorEntityDescription):
    """Extended description for Felicity inverter sensors."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda _data: None


INVERTER_SENSOR_DESCRIPTIONS: tuple[FelicityInverterSensorDescription, ...] = (
    # AC input
    FelicityInverterSensorDescription(
        key="ac_in_voltage",
        name="AC Input Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:transmission-tower",
        value_fn=lambda d: _div(_get(d, ("ACin", 0, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_in_current",
        name="AC Input Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:current-ac",
        value_fn=lambda d: _div(_get(d, ("ACin", 1, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_in_power",
        name="AC Input Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:flash",
        value_fn=lambda d: _div(_get(d, ("ACin", 2, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_in_frequency",
        name="AC Input Frequency",
        native_unit_of_measurement="Hz",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:sine-wave",
        value_fn=lambda d: _div(_get(d, ("ACin", 3, 0)), 10.0, 1),
    ),

    # AC output
    FelicityInverterSensorDescription(
        key="ac_out_voltage",
        name="AC Output Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:home-lightning-bolt",
        value_fn=lambda d: _div(_get(d, ("ACout", 0, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_out_current",
        name="AC Output Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:current-ac",
        value_fn=lambda d: _div(_get(d, ("ACout", 1, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_out_power",
        name="AC Output Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:flash",
        value_fn=lambda d: _div(_get(d, ("ACout", 2, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="ac_out_frequency",
        name="AC Output Frequency",
        native_unit_of_measurement="Hz",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:sine-wave",
        value_fn=lambda d: _div(_get(d, ("ACout", 3, 0)), 10.0, 1),
    ),

    # DC bus / load
    FelicityInverterSensorDescription(
        key="dc_bus_voltage",
        name="DC Bus Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:current-dc",
        value_fn=lambda d: _div(d.get("busVp"), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="load_percent",
        name="Load Percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:gauge",
        value_fn=lambda d: _div(d.get("lPerc"), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="power_flow",
        name="Power Flow",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:swap-horizontal",
        value_fn=lambda d: d.get("pFlow"),
    ),

    # Battery snapshot (as seen by inverter)
    FelicityInverterSensorDescription(
        key="inv_battery_soc",
        name="Battery SOC (via inverter)",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery",
        value_fn=lambda d: _div(_get(d, ("Batsoc", 0, 0)), 100.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="inv_battery_voltage",
        name="Battery Voltage (via inverter)",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:current-dc",
        value_fn=lambda d: _div(_get(d, ("Batt", 0, 0)), 1000.0, 2),
    ),
    FelicityInverterSensorDescription(
        key="inv_battery_current",
        name="Battery Current (via inverter)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:current-dc",
        value_fn=lambda d: _div(_get(d, ("Batt", 1, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="inv_battery_power",
        name="Battery Power (via inverter)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        value_fn=lambda d: _battery_power(d),
    ),

    # Temperatures
    FelicityInverterSensorDescription(
        key="temp_1",
        name="Temperature 1",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=lambda d: _div(_get(d, ("Temp", 0, 0)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="temp_2",
        name="Temperature 2",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=lambda d: _div(_get(d, ("Temp", 0, 2)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="temp_3",
        name="Temperature 3",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=lambda d: _div(_get(d, ("Temp", 0, 3)), 10.0, 1),
    ),
    FelicityInverterSensorDescription(
        key="temp_4",
        name="Temperature 4",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=lambda d: _div(_get(d, ("Temp", 0, 4)), 10.0, 1),
    ),

    # Diagnostics
    FelicityInverterSensorDescription(
        key="work_mode",
        name="Work Mode",
        icon="mdi:cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("workM"),
    ),
    FelicityInverterSensorDescription(
        key="warning_code",
        name="Warning Code",
        icon="mdi:alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("warn"),
    ),
    FelicityInverterSensorDescription(
        key="fault_code",
        name="Fault Code",
        icon="mdi:alert-octagon",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("fault"),
    ),
    FelicityInverterSensorDescription(
        key="wan2f_flags",
        name="Warning Flags (wan2F)",
        icon="mdi:flag",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("wan2F"),
    ),
)


def _battery_power(d: dict[str, Any]) -> Any:
    v_raw = _get(d, ("Batt", 0, 0))
    i_raw = _get(d, ("Batt", 1, 0))
    if v_raw is None or i_raw is None:
        return None
    try:
        v = float(v_raw) / 1000.0
        i = float(i_raw) / 10.0
        return round(v * i)
    except (TypeError, ValueError):
        return None


def create_inverter_sensors(
    coordinator: DataUpdateCoordinator,
    entry: ConfigEntry,
) -> list["FelicityInverterSensor"]:
    return [
        FelicityInverterSensor(coordinator, entry, desc)
        for desc in INVERTER_SENSOR_DESCRIPTIONS
    ]


class FelicityInverterSensor(CoordinatorEntity, SensorEntity):
    """Inverter sensors (Type=81/SubType=1036 etc)."""

    _attr_has_entity_name = True
    entity_description: FelicityInverterSensorDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        description: FelicityInverterSensorDescription,
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
            # ВАЖНО: не смешиваем устройство инвертора с батареей, если добавлены две записи.
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
    def native_value(self) -> Any:
        data: dict[str, Any] = self.coordinator.data or {}
        try:
            return self.entity_description.value_fn(data)
        except Exception:
            return None
