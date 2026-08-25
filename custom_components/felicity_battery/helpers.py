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
        1
        for value in first_row
        if isinstance(value, (int, float)) and value not in (0, 65535)
    )
    if not populated:
        return None

    # Current integration semantics: one row contains cell voltages from one
    # module; on 2+ row devices row 0 contains one voltage per module/string.
    return populated if len(cell_rows) >= 2 else 1
