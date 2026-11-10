from __future__ import annotations

from typing import Any, Mapping

_BATTERY_STATES = {
    320: "full",
    960: "standby",
    9152: "charging",
    5056: "discharging",
}


def battery_state(code: Any) -> str | None:
    """Decode a known Estate value while preserving unknown raw codes."""
    if not isinstance(code, int):
        return None
    return _BATTERY_STATES.get(code, f"unknown({code})")


_NO_READING = (0, 65535)

# Pack/string voltages in BatcelList row 0 use a /100 scale (~48-58 V ->
# raw 4800-5800), cell voltages a /1000 scale (~2.5-3.65 V -> raw
# 2500-3650). A row-0 value this far above the BMS-reported max cell
# voltage cannot be a cell reading.
_PACK_ROW_MARGIN_RAW = 500


def _valid_readings(row: Any) -> list[int | float]:
    if not isinstance(row, list):
        return []
    return [
        value
        for value in row
        if isinstance(value, (int, float)) and value not in _NO_READING
    ]


def batcel_has_pack_row(data: Mapping[str, Any]) -> bool:
    """Return True if BatcelList row 0 holds pack/string voltages.

    Modular batteries (e.g. LUX-X-96050HG01) report one voltage per pack in
    row 0 and the real cells in row 1. Single-module batteries (e.g.
    LUX-Y-48300) report the cells in row 0; some of them still send a second
    row, filled with 65535 placeholders, so the row count alone is not a
    reliable signal (issue #4).
    """
    rows = data.get("BatcelList")
    if not isinstance(rows, list) or not rows:
        return False
    first = _valid_readings(rows[0])
    if not first:
        return False

    try:
        max_cell_raw = data["BMaxMin"][0][0]
    except (IndexError, KeyError, TypeError):
        max_cell_raw = None
    if isinstance(max_cell_raw, (int, float)) and max_cell_raw not in _NO_READING:
        return max(first) > max_cell_raw + _PACK_ROW_MARGIN_RAW

    return len(rows) >= 2 and bool(_valid_readings(rows[1]))


def batcel_cell_row(data: Mapping[str, Any]) -> list[Any] | None:
    """Return the BatcelList row that holds the individual cell voltages."""
    rows = data.get("BatcelList")
    if not isinstance(rows, list) or not rows:
        return None
    index = 1 if batcel_has_pack_row(data) else 0
    if index >= len(rows) or not isinstance(rows[index], list):
        return None
    return rows[index]


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

    populated = len(_valid_readings(first_row))
    if not populated:
        return None

    # Row 0 holds either one voltage per module/string or the cells of a
    # single module - see batcel_has_pack_row().
    return populated if batcel_has_pack_row(data) else 1
