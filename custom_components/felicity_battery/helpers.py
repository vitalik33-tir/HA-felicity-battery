"""Read-only interpretation helpers for Felicity battery telemetry.

Keep BMS protocol/model assumptions here, outside Home Assistant entity code.
"""

from __future__ import annotations

from typing import Any, Mapping

_BATTERY_STATES = {
    320: "full",
    832: "standby (832; flags 0x0340)",  # Mode nibble=0; lower flags unclassified.
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
