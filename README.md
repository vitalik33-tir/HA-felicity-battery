# Felicity Battery (TCP) integration for Home Assistant

Custom integration for **Felicity FLA48200** (and similar) batteries which expose
a local TCP API on port `53970`.

Model-specific reference for this experimental branch: [P3.2 local TCP protocol](docs/P3_2_LOCAL_TCP_PROTOCOL.md) and [P3.2 model evidence matrix](docs/P3_2_MODEL_EVIDENCE.md). The protocol notes explicitly distinguish real FLA48200-P measurements from other models' unverified assumptions.

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
  - Battery SOH, rated battery capacity (configured from the official
    nameplate) and BMS capacity class (raw Batsoc field). BMS capacity class
    is hidden in the UI by default for new installations; an existing entity
    can be hidden in Settings without disabling it or changing its entity ID.
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
    Battery Discharging, Battery Standby, Cell Voltage Drift High (instant),
    Cell Voltage Drift Confirmed (>=30 mV for 5 minutes; clears at <=20 mV
    for 5 minutes). The original instant indicator remains unchanged.
  - Battery State: recognized Estate 832 is displayed as "Ожидание";
    raw Estate code, hex value, mode bits and undecoded lower bits are
    exposed as diagnostic attributes.
  - Optional read-only `BMS State Register (raw)` diagnostic entity (hidden
    from the regular UI by default, but enabled for Recorder): tracks `Bstate`
    along with `Estate`, their literal bit flags, and raw `Batt`/`BattList`
    current and voltage fields from the same response. The values are
    intentionally not converted into charge/discharge modes: this FLA48200-P
    has only been observed in mode 832 so far, and both current fields
    disagree at low currents. The existing sensors are unaffected.

### P1 shadow decoder (experimental branch)

The read-only profiles.py module adds an FLA48200-P profile. It never replaces
the legacy decoder or sends extra TCP requests. The existing hidden BMS State
Register (raw) entity gets a p1_shadow attribute: profile eligibility,
comparison count, mismatch keys and warnings, and a cutover gate.

The profile applies only to the manually configured FLA48200-P model with the
verified Type 112/SubType 7250, one physical pack, 16 valid individual cells,
matching extrema and consistent pack voltage. Other layouts fail closed to
the legacy decoder. In this device Batt current -1 means -0.1 A, whereas
BattList can use -1 as a no-channel sentinel. Their meanings are not merged.

Cutover stays disabled until charge and discharge states are verified on real
equipment. To run tests: python3 -m pytest -q. The committed sample fixture
is stripped of serial numbers and network metadata. To roll back P1, restore
commit 78a53ca of the feature branch and restart Home Assistant.

### P2.1 TCP reliability audit (experimental branch)

The existing transport remains one TCP connection per command, four
sequential read-only commands per 30-second update. P2.1 adds only safety
guards to the existing read loop, without changing command bytes, the
0.4-second inter-fragment idle timeout, response parsing formats or retries:

- 15-second total per-request deadline, including connect/write/read;
- 256 KiB maximum response payload (observed FLA48200-P replies are
  approximately 110 to 870 bytes per command);
- a bounded socket-close wait;
- rejection and normal retry of a visibly cut-off JSON object, including
  an incomplete object after an otherwise complete first object.

A timeout or oversized/incomplete reply raises FelicityApiError so the
existing retry/coordinator failure handling remains authoritative.
The basic-info, settings and date commands remain best-effort; realtime
telemetry is still mandatory. The bound does not prove that a balanced
but semantically incomplete frame is valid: model-specific payload
validation remains a separate P2.2 concern.

Tests/test_tcp_p2.py uses a local in-process fake BMS TCP server with
fragmentation, forced disconnect, retry, timeout, continuous trickle,
oversized payload and a complete four-command cycle. No battery writes,
extra polling or inverter changes are involved. P2.1 rollback: restore
GitHub commit 1be50d2 and restart Home Assistant.

### P2.2 Model-scoped runtime validation (experimental branch)

For an explicitly configured FLA48200-P (verified Type 112, SubType 7250,
single pack), the live client validates runtime data *before* publishing it:
Batt voltage/current structure; SOC range; 16 individually plausible cell
voltages; reported extrema; and pack-voltage consistency. Missing required
measurements or impossible values trigger one fresh read-only retry. If the
second reading is still invalid, the existing HA coordinator reports a
failed update rather than treating corrupted values as fresh data.

There is a safety exception: when hardware fault/warning registers contain
a nonzero code, the frame is accepted by the validator even if some readings
are implausible, so that actual BMS alarm information is not hidden by the
validation. Missing warning registers remain unknown, never interpreted as
zero.

For every other battery model, the validation step is deliberately bypassed:
legacy transport and parsing remain unchanged. In particular, valid -1 raw
Batt current readings are preserved, even though that same number can be a
sentinel in other fields.

Tested with model-specific malformed frames, standby and synthetic alarm
frames, retry/connection-loss simulation, plus real read-only FLA48200-P
queries. Does not change BMS settings or inverter controls. For a P2.2
rollback, restore feature-branch commit 64fa4e9 and restart Home Assistant.

### P2.3 Link loss and recovery (experimental branch)

Home Assistant Core 2026.11.2 DataUpdateCoordinator already serializes
scheduled and manual refreshes, marks CoordinatorEntity sensors unavailable
when an UpdateFailed occurs, and schedules further refreshes automatically
after an unsuccessful cycle. No custom reconnect task or cached-value
fallback is added.

Each complete Felicity poll now has a 25-second deadline (below the
30-second scan interval), including realtime data, three optional commands,
read retries and TCP socket cleanup. On expiry a FelicityApiError becomes
the existing UpdateFailed; the next scheduled HA poll opens fresh sockets.
The complete four-command cycle measured ~1.75 seconds on the real pack.

Tests simulate repeated remote disconnects, continued outage, first
successful reconnection, fresh measurements, an unresponsive optional
request, socket closing under cancellation, repeated timeouts with no
accumulating tasks, and the unchanged successful response structure.
No real power interruption or network disconnection was performed.
To revert P2.3, reinstall experiment-branch commit c7d66df and restart
Home Assistant.

### P2.4 Model-safe polling reduction (experimental branch)

Measured FLA48200-P TCP response times (three independent four-command
cycles): ~454 ms realtime, 427 ms basic identity/firmware, 446 ms BMS
settings, 431 ms Wi-Fi/date. Only realtime cell/current and date fields
changed in this short sample; unchanged settings in a short sample are NOT
proof that they can be polled less often.

For an explicitly configured FLA48200-P, basic identity/firmware data is
read at startup, then no less frequently than once every 300 seconds. A
local copy of its response is supplied to the existing entity readers
while still within the TTL. Firmware changes can therefore be reported up
to five minutes later during continuous service; restarting or a failed
full poll forces a fresh identity query. After an expired identity query
fails, stale values are not substituted. Only successful reads renew TTL.

The realtime telemetry, all 62 BMS configuration/limit fields, and dynamic
Wi-Fi/date status continue to be queried every 30 seconds. For ALL other
battery models, all four original commands are retained every 30 seconds.

On-device verification: four consecutive complete read-only polls required
13 connections instead of 16; the first took ~1.76 s, subsequent polls
~1.31-1.37 s. Simulated TTL expiry triggered a fresh basic-info query.
No network, BMS, charge/discharge or inverter settings changed.
P2.4 rollback: restore experimental-branch commit 0c878cd and restart HA.

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
         helpers.py
         profiles.py
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
