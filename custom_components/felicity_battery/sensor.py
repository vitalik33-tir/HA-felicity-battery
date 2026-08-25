from __future__ import annotations
# -*- coding: utf-8 -*-

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
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

DEFAULT_MODEL = "Felicity Battery (local API)"


def _fallback_model(basic: dict[str, Any]) -> str:
    """Best-effort model label when the user hasn't set one manually.
    """
    type_code = basic.get("Type")
    subtype_code = basic.get("SubType")
    if type_code is not None and subtype_code is not None:
        return f"Felicity Battery (Type {type_code}/{subtype_code})"
    return DEFAULT_MODEL


def _bcu_version(basic: dict[str, Any]) -> str | None:
    """Format the BCU (Battery Control Unit) version - M1SwVer + DSwVer,
    e.g. "203-05-86". Confirmed exact match against the FSOLAR app's own
    "Versionsdaten" screen.

    Used both for the "Battery BCU Version" sensor and as the device
    page's "Firmware" field: of the four version numbers this battery
    reports (BCU/SCU/BMU/LCD), BCU is the one Felicity's own app lists
    first, and "Control Unit" is the master pack-level controller (the
    one deciding charge/discharge behaviour, reporting Estate/workM,
    etc.) - the most reasonable single value to represent "the battery's
    firmware version" if only one can be shown. The Wi-Fi module's own
    firmware (previously shown here) stays available separately as the
    "WiFi Module FW Version" sensor.
    """
    major = basic.get("M1SwVer")
    minor = basic.get("DSwVer")
    if not isinstance(major, (int, float)) or not isinstance(minor, (int, float)):
        return None
    minor = int(minor)
    return f"{int(major)}-{minor // 100:02d}-{minor % 100:02d}"


def _estate_mode(code: Any) -> int | None:
    """Decode the charge/discharge/standby mode out of the 'Estate' field.

    Confirmed across two different physical units (a FLA48200-style pack
    the integration originally assumed, and a LUX-X-96050HG01 observed
    live): Estate = <device-specific base flags, low 12 bits> +
    mode * 0x1000. The base bits differ per device/firmware batch
    (0x0C0 vs 0x3C0 observed so far), but the mode nibble is consistent:
      0 = standby, 1 = discharging, 2 = charging.

    This is unconfirmed/reverse-engineered - Estate/workState has no
    decode table anywhere in Felicity's own app, not even for exact
    values like "full" (the one sample seen, 320, decodes as mode 0,
    same as standby). For that reason there's no dedicated sensor
    exposing this value directly; it's only used as a secondary
    fallback (behind the real current reading) in "direction" and the
    charging/discharging/standby binary sensors, never as the sole
    source of truth and never surfaced as a raw/guessed value.
    """
    if not isinstance(code, int):
        return None
    return (code >> 12) & 0xF


@dataclass
class FelicitySensorDescription(SensorEntityDescription):
    """Extended description for Felicity sensors."""


SENSOR_DESCRIPTIONS: tuple[FelicitySensorDescription, ...] = (
    # --- Main operational sensors ---
    FelicitySensorDescription(
        key="soc",
        name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
        suggested_display_precision=1,
    ),
    FelicitySensorDescription(
        key="voltage",
        name="Battery Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=2,
    ),
    FelicitySensorDescription(
        key="current",
        name="Battery Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=1,
    ),
    FelicitySensorDescription(
        key="power",
        name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    # Split charge/discharge current and power
    FelicitySensorDescription(
        key="charge_current",
        name="Battery Charge Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=1,
    ),
    FelicitySensorDescription(
        key="discharge_current",
        name="Battery Discharge Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        suggested_display_precision=1,
    ),
    FelicitySensorDescription(
        key="charge_power",
        name="Battery Charge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    FelicitySensorDescription(
        key="discharge_power",
        name="Battery Discharge Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    ),
    FelicitySensorDescription(
        key="direction",
        name="Battery Direction",
        icon="mdi:swap-vertical",
    ),
    FelicitySensorDescription(
        # NOT two independent temperature probes - confirmed against the
        # FSOLAR app ("Max. Temp. der Zelle: 28°C" / "Min. Temp. der
        # Zelle: 22°C"): this is the max/min across the pack's cell
        # temperature sensors, analogous to Max/Min Cell Voltage below.
        # Key kept as temp1 for entity continuity.
        key="temp1",
        name="Max Cell Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-high",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="temp2",
        name="Min Cell Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer-low",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Additional battery telemetry (state of health, capacity,
    # voltage limits, working mode) - values confirmed by comparing the
    # raw readings against the FSOLAR app's own displayed numbers ---
    FelicitySensorDescription(
        # Batsoc[0][1], scale /10. Matches the Cloud UI's reported 97%
        # exactly.
        key="soh",
        name="Battery SOH",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heart-pulse",
        suggested_display_precision=1,
    ),
    FelicitySensorDescription(
        # Batsoc[0][2], scale /1000. Matches the FSOLAR app's displayed
        # battery capacity (Ah) exactly.
        key="capacity_ah",
        name="Battery Capacity",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging-100",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # LVolCur[0][0], scale /10. Exact match to the charge voltage
        # limit shown in the FSOLAR app.
        key="charge_voltage_limit",
        name="Charge Voltage Limit",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # LVolCur[0][1], scale /10. Exact match to the discharge voltage
        # limit shown in the FSOLAR app.
        key="discharge_voltage_limit",
        name="Discharge Voltage Limit",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # workM, decoded using the mode table below (0=Power On,
        # 1=Standby, 2=Battery Mode, 3=Discharge only, 4=Charge only,
        # 5=Low power, 6=Fault, 7=Shutdown, 8=Test, 9=Upgrade), confirmed
        # against observed values and the FSOLAR app's own state display.
        key="working_mode",
        name="Working Mode",
        icon="mdi:state-machine",
    ),

    # --- Cell-level diagnostics ---
    FelicitySensorDescription(
        key="max_cell_v",
        name="Max Cell Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-high",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="min_cell_v",
        name="Min Cell Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-low",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_drift",
        name="Cell Voltage Drift",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-bell-curve",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Pack/string voltages (BatcelList[0]) - keys unchanged
    # (cell_N_v) for entity continuity, but renamed: these aren't
    # individual cells, they're 12 sub-pack/string voltages (~53V), see
    # BatcelList[1] further below for the real per-cell voltages. ---
    FelicitySensorDescription(
        key="cell_1_v", name="Pack 1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_2_v", name="Pack 2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_3_v", name="Pack 3 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_4_v", name="Pack 4 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_5_v", name="Pack 5 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_6_v", name="Pack 6 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_7_v", name="Pack 7 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_8_v", name="Pack 8 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_9_v", name="Pack 9 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_10_v", name="Pack 10 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_11_v", name="Pack 11 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_12_v", name="Pack 12 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_13_v", name="Pack 13 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_14_v", name="Pack 14 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_15_v", name="Pack 15 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_16_v", name="Pack 16 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Real individual cell voltages 1-16 (BatcelList[1], previously
    # never parsed/exposed at all) - confirmed against the FSOLAR app's
    # displayed max/min cell voltage. New keys, since this is a
    # different physical quantity than the pack voltages above. ---
    FelicitySensorDescription(
        key="cell_real_1_v", name="Cell 1 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_2_v", name="Cell 2 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_3_v", name="Cell 3 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_4_v", name="Cell 4 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_5_v", name="Cell 5 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_6_v", name="Cell 6 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_7_v", name="Cell 7 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_8_v", name="Cell 8 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_9_v", name="Cell 9 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_10_v", name="Cell 10 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_11_v", name="Cell 11 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_12_v", name="Cell 12 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_13_v", name="Cell 13 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_14_v", name="Cell 14 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_15_v", name="Cell 15 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_real_16_v", name="Cell 16 Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery", suggested_display_precision=3, entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Limits from runtime data ---
    FelicitySensorDescription(
        key="max_charge_current",
        name="Max Charge Current (runtime)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="max_discharge_current",
        name="Max Discharge Current (runtime)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="fault",
        name="Battery Fault Code",
        icon="mdi:alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="warning",
        name="Battery Warning Code",
        icon="mdi:alert-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Info / firmware / type / serials ---
    FelicitySensorDescription(
        # NOTE: this is the Wi-Fi module's own firmware (basic["version"]),
        # NOT a battery board version - the FSOLAR app shows it separately
        # from BCU/SCU/BMU/LCD (see below), which are the actual battery
        # firmware versions. Renamed to avoid the misleading "Battery FW"
        # label; key kept as fw_version for entity continuity.
        key="fw_version",
        name="WiFi Module FW Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # NOTE: standalone "Battery BMS M1/M2 FW" sensors (raw M1SwVer/M2SwVer)
    # were removed - they were an exact subset of BCU/SCU below (e.g.
    # M1SwVer is literally the first component of BCU: "203-05-86"),
    # so they added no information, just clutter. bms_m1_fw/bms_m2_fw keys
    # are intentionally no longer used anywhere.
    FelicitySensorDescription(
        # Confirmed against the FSOLAR app's "Versionsdaten" screen:
        # BCU = M1SwVer + DSwVer (formatted "0X-YY"), e.g. 203-05-86.
        key="bcu_version",
        name="Battery BCU Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # SCU = M2SwVer + CtHwVer (formatted "0X-YY"), e.g. 203-03-00.
        key="scu_version",
        name="Battery SCU Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # BMU = DHwVer + PwHwVer (formatted "0X-YY"), e.g. 202-03-00.
        key="bmu_version",
        name="Battery BMU Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # LCD = BDsVer as-is, e.g. 118.
        key="lcd_version",
        name="Battery LCD Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        # From 'wifilocalMonitor:get Date' (not previously queried at all).
        key="wifi_rssi",
        name="WiFi Module Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="battery_type",
        name="Battery Type",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="battery_subtype",
        name="Battery SubType",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="serial",
        name="Battery Serial",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="wifi_serial",
        name="WiFi Module Serial",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),

    # --- Settings / thresholds (dev set infor) ---
    FelicitySensorDescription(
        key="ttl_pack",
        name="Battery Pack Count",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_v_80",
        name="Cell Voltage @80%",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-80",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_v_20",
        name="Cell Voltage @20%",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-20",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_over_voltage",
        name="Cell Over Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-alert",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="cell_under_voltage",
        name="Cell Under Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-alert-outline",
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="charge_limit_setting",
        name="Charge Current Limit (BMS protection)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FelicitySensorDescription(
        key="discharge_limit_setting",
        name="Discharge Current Limit (BMS protection)",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Felicity sensors based on a config entry.

    Only add sensors that actually produced a value on the first refresh.
    Some SENSOR_DESCRIPTIONS map to fields that don't exist (or are named
    differently) on every Felicity battery model/firmware; leaving those
    entities permanently stuck on 'unknown' is just noise, so we skip them
    instead. (coordinator.async_config_entry_first_refresh() has already
    run in __init__.py by the time this is called, so coordinator.data is
    populated.)
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[FelicitySensor] = []
    skipped: list[str] = []
    for desc in SENSOR_DESCRIPTIONS:
        entity = FelicitySensor(coordinator, entry, desc)
        if entity.native_value is not None:
            entities.append(entity)
        else:
            skipped.append(desc.key)

    if skipped:
        # Not an error - just informational, in case a future firmware
        # update starts reporting one of these fields.
        import logging

        logging.getLogger(__name__).info(
            "Felicity Battery: skipping sensors with no data on this device: %s",
            ", ".join(skipped),
        )

    async_add_entities(entities)


class FelicitySensor(CoordinatorEntity, SensorEntity):
    """Representation of a Felicity sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        description: FelicitySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info to group entities into one device."""
        data = self.coordinator.data or {}
        serial = data.get("DevSN") or data.get("wifiSN") or self._entry.entry_id
        basic = data.get("_basic") or {}
        # Show the BCU (Battery Control Unit) version here - see
        # _bcu_version() for why that one was picked among the battery's
        # four version numbers. Falls back to the Wi-Fi module's own
        # firmware if BCU data isn't available (e.g. a different device/
        # firmware that doesn't expose M1SwVer/DSwVer under those names).
        sw_version = _bcu_version(basic) or basic.get("version")
        host = self._entry.data.get(CONF_HOST)
        serial_display = f"{serial} ({host})" if host else serial
        model = self._entry.data.get("model") or _fallback_model(basic)

        return {
            "identifiers": {(DOMAIN, serial)},
            "name": self._entry.data.get("name", "Felicity Battery"),
            "manufacturer": "Felicity",
            "model": model,
            "sw_version": sw_version,
            "serial_number": serial_display,
        }

    @property
    def name(self) -> str | None:
        """Dynamic display name for entities whose meaning depends on
        runtime data - see the long comment in native_value() for
        cell_*_v. Everything else falls back to the static description
        name as usual."""
        key = self.entity_description.key
        if key.startswith("cell_") and key.endswith("_v") and not key.startswith("cell_real_"):
            try:
                n = int(key.split("_")[1])
            except (ValueError, IndexError):
                n = "?"
            cells_list = (self.coordinator.data or {}).get("BatcelList")
            if isinstance(cells_list, list) and len(cells_list) >= 2:
                return f"Pack {n} Voltage"
            return f"Cell {n} Voltage"
        return self.entity_description.name

    @property
    def native_value(self) -> Any:
        """Return the native value of the entity."""
        data: dict = self.coordinator.data or {}
        key = self.entity_description.key

        def get_nested(path: tuple[Any, ...]):
            cur: Any = data
            try:
                for p in path:
                    cur = cur[p]
                return cur
            except (KeyError, IndexError, TypeError):
                return None

        # --- Runtime telemetry ---
        if key == "soc":
            raw = get_nested(("Batsoc", 0, 0))
            return round(raw / 100, 1) if raw is not None else None

        if key == "voltage":
            raw = get_nested(("Batt", 0, 0))
            return round(raw / 1000, 2) if raw is not None else None

        if key == "current":
            raw = get_nested(("Batt", 1, 0))
            return round(raw / 10, 1) if raw is not None else None

        if key == "power":
            v_raw = get_nested(("Batt", 0, 0))
            i_raw = get_nested(("Batt", 1, 0))
            if v_raw is None or i_raw is None:
                return None
            v = v_raw / 1000
            i = i_raw / 10
            return round(v * i)

        if key == "charge_current":
            i_raw = get_nested(("Batt", 1, 0))
            if i_raw is None:
                return None
            current = i_raw / 10.0
            return round(current, 1) if current > 0 else 0.0

        if key == "discharge_current":
            i_raw = get_nested(("Batt", 1, 0))
            if i_raw is None:
                return None
            current = i_raw / 10.0
            return round(-current, 1) if current < 0 else 0.0

        if key == "charge_power":
            v_raw = get_nested(("Batt", 0, 0))
            i_raw = get_nested(("Batt", 1, 0))
            if v_raw is None or i_raw is None:
                return None
            v = v_raw / 1000.0
            i = i_raw / 10.0
            p = v * i
            return round(p) if p > 0 else 0

        if key == "discharge_power":
            v_raw = get_nested(("Batt", 0, 0))
            i_raw = get_nested(("Batt", 1, 0))
            if v_raw is None or i_raw is None:
                return None
            v = v_raw / 1000.0
            i = i_raw / 10.0
            p = v * i
            return round(-p) if p < 0 else 0

        if key == "direction":
            i_raw = get_nested(("Batt", 1, 0))
            if i_raw is not None:
                current = i_raw / 10.0
                if current > 0.05:
                    return "charging"
                if current < -0.05:
                    return "discharging"
            mode = _estate_mode(data.get("Estate"))
            if mode == 2:
                return "charging"
            if mode == 1:
                return "discharging"
            return "idle"

        if key == "temp1":
            raw = get_nested(("BTemp", 0, 0))
            return round(raw / 10, 1) if raw is not None else None

        if key == "temp2":
            raw = get_nested(("BTemp", 0, 1))
            return round(raw / 10, 1) if raw is not None else None

        if key == "soh":
            raw = get_nested(("Batsoc", 0, 1))
            return round(raw / 10, 1) if raw is not None else None

        if key == "capacity_ah":
            raw = get_nested(("Batsoc", 0, 2))
            return round(raw / 1000, 1) if raw is not None else None

        if key == "charge_voltage_limit":
            raw = get_nested(("LVolCur", 0, 0))
            return round(raw / 10, 1) if raw is not None else None

        if key == "discharge_voltage_limit":
            raw = get_nested(("LVolCur", 0, 1))
            return round(raw / 10, 1) if raw is not None else None

        if key == "working_mode":
            code = data.get("workM")
            modes = {
                0: "power_on", 1: "standby", 2: "battery_mode",
                3: "discharge_only", 4: "charge_only", 5: "low_power",
                6: "fault", 7: "shutdown", 8: "test", 9: "upgrade",
            }
            if code is None:
                return None
            return modes.get(code, f"unknown({code})")

        if key == "max_cell_v":
            raw = get_nested(("BMaxMin", 0, 0))
            return round(raw / 1000, 3) if raw is not None else None

        if key == "min_cell_v":
            raw = get_nested(("BMaxMin", 0, 1))
            return round(raw / 1000, 3) if raw is not None else None

        if key == "cell_drift":
            max_raw = get_nested(("BMaxMin", 0, 0))
            min_raw = get_nested(("BMaxMin", 0, 1))
            if max_raw is None or min_raw is None:
                return None
            return round((max_raw - min_raw) / 1000, 3)

        # --- Pack/string OR real cell voltages (BatcelList) - ADAPTIVE,
        # see also the `name` property override below.
        #
        # Backward-compat note: the meaning of BatcelList row 0 is NOT the
        # same on every Felicity battery model. On this integration's own
        # test unit (a multi-module pack), row 0 turned out to be
        # pack/string-level voltages (~53V each, /100 scale) rather than
        # individual cells, with the real per-cell data only showing up in
        # a second array (row 1, /1000 scale) - confirmed against the
        # FSOLAR app's own max/min cell voltage display. The original,
        # unpatched integration (written for a different, presumably
        # single-module battery) only ever saw one array and treated row 0
        # directly as real per-cell voltage (/1000) - which is exactly
        # right for that kind of device, just wrong for a multi-module one
        # like this integration's test unit.
        #
        # Rather than hardcoding one interpretation and risking silently
        # wrong values on whichever device shape wasn't tested, this code
        # branches on how many BatcelList arrays the device actually sends
        # at runtime:
        #   - 1 array  -> "cell_N_v" behaves exactly like the original,
        #     unpatched integration (row 0, /1000, "Cell N Voltage"), so a
        #     single-module-style upgrade is a complete no-op for these
        #     entities: same key, same scale, same name, same history.
        #     "cell_real_N_v" stays unpopulated (filtered out at setup).
        #   - 2+ arrays -> "cell_N_v" becomes "Pack N Voltage" (/100, row
        #     0) as on this integration's own test unit, and the new
        #     "cell_real_N_v" entities (row 1, /1000) provide the true
        #     per-cell readings.
        if key.startswith("cell_") and key.endswith("_v") and not key.startswith("cell_real_"):
            cells_list = get_nested(("BatcelList",))
            if not isinstance(cells_list, list) or not cells_list:
                return None
            try:
                idx = int(key.split("_")[1]) - 1  # 0..15
            except (ValueError, IndexError):
                return None
            raw = get_nested(("BatcelList", 0, idx))
            # 65535 is this device's explicit "no reading" sentinel; 0 is
            # what unpopulated slots come back as - treat both as "not
            # present" so the setup filter drops these entities instead of
            # pinning them at a permanent, misleading 0 V.
            if raw is None or raw in (0, 65535):
                return None
            if len(cells_list) >= 2:
                return round(raw / 100.0, 2)
            return round(raw / 1000.0, 3)

        if key.startswith("cell_real_") and key.endswith("_v"):
            cells_list = get_nested(("BatcelList",))
            # Only meaningful on multi-array devices - see above. On a
            # single-array device this stays unpopulated on purpose so it
            # doesn't show up as a confusing duplicate of cell_N_v.
            if not isinstance(cells_list, list) or len(cells_list) < 2:
                return None
            try:
                idx = int(key.split("_")[2]) - 1  # 0..15
            except (ValueError, IndexError):
                return None
            raw = get_nested(("BatcelList", 1, idx))
            if raw is None or raw in (0, 65535):
                return None
            return round(raw / 1000.0, 3)

        # --- Limits from runtime data ---
        if key == "max_charge_current":
            raw = get_nested(("LVolCur", 1, 0))
            return round(raw / 10, 1) if raw is not None else None

        if key == "max_discharge_current":
            raw = get_nested(("LVolCur", 1, 1))
            return round(raw / 10, 1) if raw is not None else None

        if key == "fault":
            v = data.get("Bfault")
            if v is None:
                return None
            return int(v)

        if key == "warning":
            v = data.get("Bwarn")
            if v is None:
                return None
            return int(v)

        # --- Basic info / firmware / type ---
        basic = data.get("_basic") or {}
        settings = data.get("_settings") or {}

        if key == "fw_version":
            return basic.get("version")

        if key == "bcu_version":
            return _bcu_version(basic)

        if key in ("scu_version", "bmu_version"):
            def _fmt(major: Any, minor: Any) -> str | None:
                if not isinstance(major, (int, float)) or not isinstance(minor, (int, float)):
                    return None
                minor = int(minor)
                return f"{int(major)}-{minor // 100:02d}-{minor % 100:02d}"

            if key == "scu_version":
                return _fmt(basic.get("M2SwVer"), basic.get("CtHwVer"))
            if key == "bmu_version":
                return _fmt(basic.get("DHwVer"), basic.get("PwHwVer"))

        if key == "lcd_version":
            v = basic.get("BDsVer")
            return str(v) if v is not None else None

        if key == "wifi_rssi":
            date_info = data.get("_date") or {}
            v = date_info.get("rssi")
            return int(v) if isinstance(v, (int, float)) else None

        if key == "battery_type":
            return basic.get("Type")

        if key == "battery_subtype":
            return basic.get("SubType")

        if key == "serial":
            return data.get("DevSN") or data.get("wifiSN")

        if key == "wifi_serial":
            return data.get("wifiSN")

        # --- Settings / thresholds ---
        if key == "ttl_pack":
            # The device's own 'ttlPack' field (in _settings) is transport
            # metadata (how many concatenated JSON objects make up the
            # "get dev set infor" reply), NOT the physical pack count.
            # BMSpara[0][0] reports the actual pack/module count directly
            # and matches this device's real module count exactly - use
            # that instead. Falls back to counting populated BatcelList[0]
            # slots (which independently gives the same number) if
            # BMSpara is ever missing.
            official = get_nested(("BMSpara", 0, 0))
            if isinstance(official, (int, float)) and official > 0:
                return int(official)
            cells = get_nested(("BatcelList", 0))
            if not isinstance(cells, list):
                return None
            count = sum(
                1 for v in cells if isinstance(v, (int, float)) and v not in (0, 65535)
            )
            return count or None

        if key == "cell_v_80":
            raw = settings.get("wCVP80")
            return round(raw / 1000, 3) if isinstance(raw, (int, float)) else None

        if key == "cell_v_20":
            raw = settings.get("wCVP20")
            return round(raw / 1000, 3) if isinstance(raw, (int, float)) else None

        if key == "cell_over_voltage":
            raw = settings.get("cVolHi")
            return round(raw / 1000, 3) if isinstance(raw, (int, float)) else None

        if key == "cell_under_voltage":
            raw = settings.get("cVolLo")
            return round(raw / 1000, 3) if isinstance(raw, (int, float)) else None

        if key == "charge_limit_setting":
            raw = settings.get("bCCHi2")
            return round(raw / 10, 1) if isinstance(raw, (int, float)) else None

        if key == "discharge_limit_setting":
            raw = settings.get("bDCHi2")
            return round(raw / 10, 1) if isinstance(raw, (int, float)) else None

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes for some sensors."""
        data: dict = self.coordinator.data or {}
        key = self.entity_description.key

        # Per-cell aggregation for the cell_drift sensor
        # NOTE: uses BatcelList[1] (the real per-cell array, ~3.3V each,
        # confirmed against the FSOLAR app) - it previously read
        # BatcelList[0], which is actually the pack/string voltages
        # (~53V) and produced meaningless "cell" attributes here.
        if key == "cell_drift":
            attrs: dict[str, Any] = {}
            cells_list = data.get("BatcelList")
            if (
                isinstance(cells_list, list)
                and len(cells_list) > 1
                and isinstance(cells_list[1], list)
            ):
                raw_cells = cells_list[1]
                cells_v: list[float] = []
                for c in raw_cells:
                    if isinstance(c, int) and c not in (0, 65535):
                        cells_v.append(round(c / 1000.0, 3))
                if cells_v:
                    attrs["cells"] = cells_v
                    max_v = max(cells_v)
                    min_v = min(cells_v)
                    attrs["max_cell_voltage"] = max_v
                    attrs["min_cell_voltage"] = min_v
                    attrs["max_cell_index"] = cells_v.index(max_v) + 1
                    attrs["min_cell_index"] = cells_v.index(min_v) + 1
            return attrs or None

        if key == "ttl_pack":
            attrs: dict[str, Any] = {}
            settings = data.get("_settings") or {}
            if "ttlPack" in settings:
                attrs["device_reported_ttlPack"] = settings.get("ttlPack")
                attrs["note"] = (
                    "device_reported_ttlPack is transport/packet-count "
                    "metadata from the device's own protocol, not the "
                    "physical pack/module count. The sensor state above is "
                    "derived instead from the number of populated entries "
                    "in BatcelList[0] (realtime payload)."
                )
            cells_list = data.get("BatcelList")
            if (
                isinstance(cells_list, list)
                and cells_list
                and isinstance(cells_list[0], list)
            ):
                attrs["batcel_list_0_raw"] = cells_list[0]
            return attrs or None

        if key in {
            "fw_version",
            "bcu_version",
            "scu_version",
            "bmu_version",
            "lcd_version",
            "battery_type",
            "battery_subtype",
            "serial",
            "wifi_serial",
        }:
            basic = data.get("_basic")
            if isinstance(basic, dict):
                return basic
            return None

        if key == "wifi_rssi":
            date_info = data.get("_date")
            if isinstance(date_info, dict):
                return date_info
            return None

        if key in {"charge_limit_setting", "discharge_limit_setting"}:
            attrs: dict[str, Any] = {}
            settings = data.get("_settings")
            if isinstance(settings, dict):
                attrs.update(settings)
            attrs["note"] = (
                "This reads the BMS's own protection/cutoff threshold "
                "(bCCHi2/bDCHi2 in the raw settings) - it's a hardware "
                "safety ceiling, not necessarily the charge-current limit "
                "you configure in the FSOLAR app. That app setting is most "
                "likely enforced on the inverter/charger side and doesn't "
                "appear to be exposed by this battery-only local API."
            )
            return attrs

        if key in {
            "cell_v_80",
            "cell_v_20",
            "cell_over_voltage",
            "cell_under_voltage",
        }:
            settings = data.get("_settings")
            if isinstance(settings, dict):
                return settings
            return None

        return None
