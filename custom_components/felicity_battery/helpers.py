"""Read-only interpretation helpers for Felicity battery telemetry.

Keep BMS protocol/model assumptions here, outside Home Assistant entity code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

_BATTERY_STATES = {
    320: "full",
    832: "Ожидание",  # Mode nibble=0; preserve raw bitfield in sensor attributes.
    960: "standby",
    9152: "charging",
    5056: "discharging",
}

# Millivolts: values outside this range cannot be individual LFP cells.
_CELL_MV_MIN = 2000
_CELL_MV_MAX = 4500


def battery_state(code: Any) -> str | None:
    """Decode confirmed Estate values; do not guess unknown firmware codes."""
    if not isinstance(code, int):
        return None
    return _BATTERY_STATES.get(code, f"unknown({code})")


def battery_pack_count(data: Mapping[str, Any]) -> int | None:
    """Return a conservative physical battery/module count."""
    bms_parameters = data.get("BMSpara")
    try:
        official = bms_parameters[0][0]
    except (IndexError, KeyError, TypeError):
        official = None
    if isinstance(official, (int, float)) and official > 0:
        return int(official)

    cell_rows = data.get("BatcelList")
    if not isinstance(cell_rows, list) or not cell_rows:
        return None
    first_row = cell_rows[0]
    if not isinstance(first_row, list):
        return None
    populated = sum(
        1 for value in first_row
        if isinstance(value, (int, float)) and value not in (0, 65535)
    )
    if not populated:
        return None
    # Do not count 16 individual cell readings as 16 physical battery packs.
    if cell_voltage_row(data) == 0:
        return 1
    return populated if len(cell_rows) >= 2 else 1


def _nested(data: Mapping[str, Any], *path: Any) -> Any:
    value: Any = data
    try:
        for part in path:
            value = value[part]
    except (KeyError, IndexError, TypeError):
        return None
    return value


def _valid_cell_mv(value: Any) -> bool:
    return type(value) is int and _CELL_MV_MIN <= value <= _CELL_MV_MAX


def cell_voltage_row(data: Mapping[str, Any]) -> int | None:
    """Find a physically plausible row of *cells*, not modules.

    Both one-row and multi-row payloads exist. The number of BatcelList
    rows alone cannot tell which row contains individual cells.

    For a row to be trusted, its voltage extrema must match BMaxMin OR
    its sum must agree with the measured series-pack voltage. This also
    protects other models from accidentally applying our FLA48200-P fix.
    """
    rows = data.get("BatcelList")
    if not isinstance(rows, list):
        return None
    pack_mv = _nested(data, "Batt", 0, 0)
    max_mv = _nested(data, "BMaxMin", 0, 0)
    min_mv = _nested(data, "BMaxMin", 0, 1)
    for idx, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        valid = [v for v in row if _valid_cell_mv(v)]
        populated = [v for v in row if isinstance(v, (int, float)) and v not in (0, 65535, 32767, -1)]
        if len(valid) < 4 or len(valid) != len(populated):
            continue
        extrema_match = (
            isinstance(max_mv, int) and isinstance(min_mv, int)
            and abs(max(valid) - max_mv) <= 20
            and abs(min(valid) - min_mv) <= 20
        )
        voltage_match = (
            isinstance(pack_mv, (int, float)) and pack_mv > 0
            and abs(sum(valid) - pack_mv) <= max(800, 0.02 * pack_mv)
        )
        if extrema_match or voltage_match:
            return idx
    return None


def primary_row_is_cells(data: Mapping[str, Any]) -> bool:
    return cell_voltage_row(data) == 0


def cell_voltage_mv(data: Mapping[str, Any], index: int) -> int | None:
    """Read the nth cell, retaining physical slot indexes for absent cells."""
    row_index = cell_voltage_row(data)
    if row_index is None:
        return None
    value = _nested(data, "BatcelList", row_index, index)
    return value if _valid_cell_mv(value) else None


def cell_extrema_indices(data: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Return (max_cell_number, min_cell_number), with 1-based numbering.

    Prefer the BMS' own 0-based positions only if they agree with the
    individual voltages. Otherwise use the measured row and preserve
    original positions in case intermediate cells are absent.
    """
    row_index = cell_voltage_row(data)
    if row_index is None:
        return (None, None)
    row = _nested(data, "BatcelList", row_index)
    if not isinstance(row, list):
        return (None, None)
    present = [(i, v) for i, v in enumerate(row) if _valid_cell_mv(v)]
    if not present:
        return (None, None)

    positions: list[int | None] = []
    for col, fn in ((0, max), (1, min)):
        target = fn(v for _, v in present)
        bms_position = _nested(data, "BMaxMin", 1, col)
        if (type(bms_position) is int and 0 <= bms_position < len(row)
                and _valid_cell_mv(row[bms_position])
                and abs(row[bms_position] - target) <= 2):
            positions.append(bms_position + 1)
        else:
            positions.append(next(i + 1 for i, v in present if v == target))
    return (positions[0], positions[1])


class CellDriftConfirmation:
    """Separate warning, with hysteresis and sustained-sample confirmation.

    The legacy Cell Voltage Drift High remains an immediate indication.
    On startup a new alarm requires a full confirmation period. Loss of
    data cancels an in-progress transition, without fabricating OK.
    """

    def __init__(
        self,
        *,
        on_threshold_mv: int = 30,
        off_threshold_mv: int = 20,
        delay_seconds: int = 300,
        max_sample_gap_seconds: int = 90,
    ) -> None:
        if (
            not 0 <= off_threshold_mv < on_threshold_mv
            or delay_seconds <= 0
            or max_sample_gap_seconds <= 0
        ):
            raise ValueError("Invalid threshold hysteresis, delay or sample gap")
        self.on_threshold_mv = on_threshold_mv
        self.off_threshold_mv = off_threshold_mv
        self.delay_seconds = delay_seconds
        self.max_sample_gap_seconds = max_sample_gap_seconds
        self.active = False
        self.has_valid_sample = False
        self.last_spread_mv: int | None = None
        self.pending_target: bool | None = None
        self._pending_since: datetime | None = None
        self._last_sample_at: datetime | None = None

    def update(self, spread_mv: int | None, now: datetime) -> bool:
        """Consume one measurement at a known timestamp."""
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("An aware datetime is required")
        if type(spread_mv) is not int or spread_mv < 0:
            self.last_spread_mv = None
            self.pending_target = None
            self._pending_since = None
            self._last_sample_at = None
            return self.active

        # A sample after a long network outage does not prove that
        # the condition persisted throughout the missing interval.
        if self._last_sample_at is not None:
            sample_gap = (now - self._last_sample_at).total_seconds()
            if sample_gap < 0 or sample_gap > self.max_sample_gap_seconds:
                self.pending_target = None
                self._pending_since = None
        self._last_sample_at = now
        self.has_valid_sample = True
        self.last_spread_mv = spread_mv
        # Distinct thresholds keep the alert stable at 21..29mV.
        transition = (
            spread_mv <= self.off_threshold_mv
            if self.active else spread_mv >= self.on_threshold_mv
        )
        if not transition:
            self.pending_target = None
            self._pending_since = None
            return self.active

        desired = not self.active
        if self.pending_target != desired or self._pending_since is None:
            self.pending_target = desired
            self._pending_since = now
        elif now >= self._pending_since and (
            now - self._pending_since
        ).total_seconds() >= self.delay_seconds:
            self.active = desired
            self.pending_target = None
            self._pending_since = None
        return self.active


def bms_state_snapshot(data: Mapping[str, Any]) -> dict[str, Any]:
    """Expose raw state and current registers without decoding their meaning.

    These fields originate from one real-time response. This diagnostic
    snapshot does not change any existing operating-mode/current sensors.
    Unknown or sentinel state values are never interpreted as Standby.
    """
    def _valid_register(value: Any) -> int | None:
        if type(value) is not int or value < 0 or value in (32767, 65535):
            return None
        return value

    bstate = _valid_register(data.get("Bstate"))
    estate = _valid_register(data.get("Estate"))
    return {
        "bstate_raw": bstate,
        "bstate_hex": f"0x{bstate:04X}" if bstate is not None else None,
        "estate_raw": estate,
        "estate_hex": f"0x{estate:04X}" if estate is not None else None,
        "bstate_equals_estate": (
            bstate == estate if bstate is not None and estate is not None else None
        ),
        "bstate_bit_12": bool(bstate & 0x1000) if bstate is not None else None,
        "bstate_bit_13": bool(bstate & 0x2000) if bstate is not None else None,
        "bstate_bit_14": bool(bstate & 0x4000) if bstate is not None else None,
        # Preserve both channels verbatim (including their -1 and 65535
        # values), because scaling, sentinels and direction are not yet
        # verified across battery models and operating conditions.
        "batt_current_raw": _nested(data, "Batt", 1, 0),
        "battlist_current_raw": _nested(data, "BattList", 1, 0),
        "batt_voltage_raw": _nested(data, "Batt", 0, 0),
        "battlist_voltage_raw": _nested(data, "BattList", 0, 0),
    }


def format_firmware_components(major: Any, minor: Any) -> str | None:
    """Format a reported composite FW version without treating sentinels as digits.

    For example, DSwVer=65535 denotes an absent field, not the decimal
    suffix 655-35. Keep valid major components visible with a clear
    partial marker; never invent the missing suffix. The unmodified raw
    components remain available in the version sensor's attributes.
    """
    if major is None:
        return None
    if (
        type(major) not in (int, float)
        or not float(major).is_integer()
        or major < 0
        or major in (32767, 65535)
    ):
        return "Не определена"
    major_value = int(major)
    missing_minor = (
        type(minor) not in (int, float)
        or not float(minor).is_integer()
        or minor < 0
        or minor in (32767, 65535)
    )
    if missing_minor:
        if major_value == 0:
            return "Не определена"
        return f"{major_value} (неполная версия)"

    minor_value = int(minor)
    return f"{major_value}-{minor_value // 100:02d}-{minor_value % 100:02d}"
