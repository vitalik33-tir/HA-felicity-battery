# Felicity Battery (TCP) integration for Home Assistant

Custom integration for **Felicity FLA48200** (and similar) batteries which expose
a local TCP API on port `53970`.

The integration:

- connects to `IP:PORT` of the Felicity Wi-Fi module,
- sends `wifilocalMonitor:get dev real infor` (plus `get dev basice infor`,
  `get dev set infor` and `get Date` for firmware/settings/signal data),
- parses the JSON response(s),
- creates **one device** in Home Assistant with multiple sensors:
  - SOC, voltage, current, power (plus separate charge/discharge current
    and power sensors)
  - Battery Direction (charging/discharging/idle)
  - Battery State (full/standby/charging/discharging or the unknown raw code)
  - Max/Min Cell Temperature
  - Max/Min Cell Voltage and Cell Voltage Drift
  - Pack/Cell voltages for up to 16 cells - automatically labeled and
    scaled as either "Pack N Voltage" or "Cell N Voltage" depending on
    what the connected battery actually reports (single- vs. multi-array
    packs)
  - Battery SOH, Battery Capacity (Ah)
  - Charge/Discharge Voltage Limit, Max Charge/Discharge Current (runtime)
  - Working Mode (official Felicity enum, e.g. Standby/Battery Mode/Fault/...)
  - Battery Pack Count
  - Battery BMS M1/M2 raw firmware values (legacy entity compatibility),
    formatted BCU/SCU/BMU/LCD firmware versions, WiFi Module FW Version
  - WiFi Module Signal (RSSI)
  - Battery Type/SubType, Battery Serial, WiFi Module Serial
  - Fault/Warning codes and settings-derived sensors (Cell Over/Under
    Voltage, Cell Voltage @20%/@80%, Charge/Discharge Current Limit
    from BMS protection settings)
  - binary sensors: Battery Fault, Battery Warning, Battery Charging,
    Battery Discharging, Battery Standby, Cell Voltage Drift High

Model-specific sensors are created only when your battery/firmware reports
their data. Sensors backed by the supplementary basic/settings/date commands
are kept when one of those commands fails transiently during startup, allowing
them to recover automatically on a later poll instead of requiring a reload.

Tested with **FLA48200** and **LUX-X-96050HG01** batteries, communicating
with the Wi-Fi module (e.g. `IOTH2407`) on TCP port `53970`.

## Installation

1. Copy this repository into your Home Assistant `config` directory, so that you have:

   ```text
   config/
     custom_components/
       felicity_battery/
         __init__.py
         manifest.json
         const.py
         api.py
         config_flow.py
         sensor.py
         binary_sensor.py
   ```

2. Restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration**.

4. Search for **"Felicity Battery (TCP)"**.

5. Enter:
   - **Name** – any friendly name (e.g. `Felicity FLA48200`),
   - **Host** – IP of the Wi-Fi module (e.g. `192.168.1.68`),
   - **Port** – usually `53970`.
   - **Model** *(optional)* – your battery's marketing model name (e.g.
     `FLA48200`, `LUX-X-96050HG01`). The device only reports numeric
     Type/SubType codes, not a human-readable model, so this can't be
     detected automatically; leave it blank to fall back to a generic
     label showing those codes instead.

After that you should see one device with multiple sensors.

## Disclaimer

This integration uses an **unofficial local API** discovered by traffic analysis.
It is not affiliated with Felicity. Use at your own risk.
