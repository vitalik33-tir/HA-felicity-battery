"""Conservative, read-only profiles for Felicity battery telemetry.

P1: This module never changes the transport, existing sensors or BMS.
The FLA48200-P mapping is independently checked against legacy entities.
Other layouts use the existing integration until separately verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

MODEL = "FLA48200-P"
TYPE = 112
SUBTYPE = 7250
CELLS = 16
SENTINELS = frozenset((-1, 32767, 65535))


@dataclass(frozen=True)
class ProfileResult:
    """Decoded candidate values, not automatically authoritative."""

    profile: str
    valid: bool
    reason: str
    values: dict[str, Any]
    warnings: tuple[str, ...] = ()


def _raw(data: Mapping[str, Any], key: str, row: int, col: int) -> int | None:
    """Retrieve a single numeric raw channel, rejecting sentinel values."""
    try:
        value = data[key][row][col]
    except (IndexError, KeyError, TypeError):
        return None
    # -1 is a valid current (-0.1 A) in Batt[1][0] on FLA48200-P.
    # Other fields must be range-checked separately.
    return value if type(value) is int and value not in (32767, 65535) else None


def _register(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if type(value) is int and value not in SENTINELS else None


def _scaled(value: int | None, divisor: int, digits: int) -> float | None:
    return round(value / divisor, digits) if value is not None else None


def _position(
    data: Mapping[str, Any], cells: list[int], col: int
) -> int:
    """Verify a 0-based BMS slot; otherwise use the measured extrema.

    Equal-voltage cells can have different equally valid extrema positions.
    Match the old integration's verified BMS index preference.
    """
    target = max(cells) if col == 0 else min(cells)
    bms_pos = _raw(data, "BMaxMin", 1, col)
    if (
        bms_pos is not None
        and 0 <= bms_pos < len(cells)
        and abs(cells[bms_pos] - target) <= 2
    ):
        return bms_pos + 1
    return next(i + 1 for i, value in enumerate(cells) if value == target)


def decode_fla48200p(
    data: Mapping[str, Any], *, configured_model: str | None
) -> ProfileResult:
    """Decode only a securely matching one-pack FLA48200-P layout.

    Model label comes from the explicitly configured nameplate, NOT the
    numerical SubType capacity class. The fingerprint is deliberately
    restrictive: a model mismatch fails closed to the legacy decoder.
    """
    invalid = lambda reason: ProfileResult(MODEL, False, reason, {})

    if not isinstance(configured_model, str) or configured_model.strip().upper() != MODEL:
        return invalid("model_not_explicitly_configured")
    if data.get("Type") != TYPE or data.get("SubType") != SUBTYPE:
        return invalid("device_type_subtype_mismatch")
    if _raw(data, "BMSpara", 0, 0) != 1:
        return invalid("not_a_verified_single_pack")

    rows = data.get("BatcelList")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        return invalid("missing_cell_row")
    if len(rows[0]) != CELLS:
        return invalid("unexpected_cell_count")
    cells = rows[0]
    if any(type(v) is not int or not 2000 <= v <= 4500 for v in cells):
        return invalid("invalid_cell_millivolts")

    max_mv = _raw(data, "BMaxMin", 0, 0)
    min_mv = _raw(data, "BMaxMin", 0, 1)
    if max_mv is None or min_mv is None or max_mv < min_mv:
        return invalid("missing_or_inverted_extrema")
    if abs(max(cells) - max_mv) > 20 or abs(min(cells) - min_mv) > 20:
        return invalid("cell_extrema_do_not_match")

    pack_mv = _raw(data, "Batt", 0, 0)
    if pack_mv is None or not 38000 <= pack_mv <= 65000:
        return invalid("invalid_pack_voltage")
    if abs(sum(cells) - pack_mv) > 800:
        return invalid("cells_do_not_sum_to_pack_voltage")

    current_raw = _raw(data, "Batt", 1, 0)
    current = _scaled(current_raw, 10, 1)
    voltage = _scaled(pack_mv, 1000, 3)
    bstate = _register(data, "Bstate")
    estate = _register(data, "Estate")
    soc_raw = _raw(data, "Batsoc", 0, 0)
    soh_raw = _raw(data, "Batsoc", 0, 1)
    cap_raw = _raw(data, "Batsoc", 0, 2)

    # These values reproduce current sensor semantics for parity.
    # They are not claimed to be validated physical current under load.
    mode = ((estate >> 12) & 15) if estate is not None else None
    if current is not None and current > 0.05:
        direction = "charging"
    elif current is not None and current < -0.05:
        direction = "discharging"
    elif mode == 2:
        direction = "charging"
    elif mode == 1:
        direction = "discharging"
    else:
        direction = "idle"

    value_map: dict[str, Any] = {
        "soc": _scaled(soc_raw, 100, 1),
        "soh": _scaled(soh_raw, 10, 1),
        "capacity_ah": _scaled(cap_raw, 1000, 1),
        "voltage": _scaled(pack_mv, 1000, 2),
        "current": current,
        "power": round(voltage * current) if current is not None else None,
        "charge_current": max(current, 0.0) if current is not None else None,
        "discharge_current": max(-current, 0.0) if current is not None else None,
        "charge_power": max(round(voltage * current), 0) if current is not None else None,
        "discharge_power": max(round(-voltage * current), 0) if current is not None else None,
        "direction": direction,
        "temp1": _scaled(_raw(data, "BTemp", 0, 0), 10, 1),
        "temp2": _scaled(_raw(data, "BTemp", 0, 1), 10, 1),
        "max_cell_v": _scaled(max_mv, 1000, 3),
        "min_cell_v": _scaled(min_mv, 1000, 3),
        "cell_drift": _scaled(max_mv - min_mv, 1000, 3),
        "max_cell_index": _position(data, cells, 0),
        "min_cell_index": _position(data, cells, 1),
        "ttl_pack": 1,
        "charge_voltage_limit": _scaled(_raw(data, "LVolCur", 0, 0), 10, 1),
        "discharge_voltage_limit": _scaled(_raw(data, "LVolCur", 0, 1), 10, 1),
        "max_charge_current": _scaled(_raw(data, "LVolCur", 1, 0), 10, 1),
        "max_discharge_current": _scaled(_raw(data, "LVolCur", 1, 1), 10, 1),
        "fault": _register(data, "Bfault"),
        "warning": _register(data, "Bwarn"),
        # Only the exact 832->Standby mapping was observed on this model.
        # Do not infer charge/discharge labels from other untested codes.
        "state": "Ожидание" if estate == 832 else None,
    }
    for idx, mv in enumerate(cells, 1):
        value_map[f"cell_{idx}_v"] = _scaled(mv, 1000, 3)

    warnings: list[str] = []
    if bstate is None:
        warnings.append("bstate_missing_or_invalid")
    elif estate is not None and bstate != estate:
        warnings.append("bstate_estate_mismatch")
    if estate not in (None, 832):
        warnings.append("estate_outside_verified_standby")
    if current_raw is not None and abs(current_raw) > 10:
        warnings.append("current_under_load_not_yet_verified")
    if abs(sum(cells) - pack_mv) > 200:
        warnings.append("pack_voltage_difference_over_200mv")

    return ProfileResult(MODEL, True, "verified_layout", value_map, tuple(warnings))


def compare_with_legacy(
    result: ProfileResult,
    read_legacy: Callable[[str], Any],
) -> dict[str, Any]:
    """Compare independently decoded values to existing HA sensor logic.

    No entity state is changed. Return a bounded diagnostic report only.
    """
    if not result.valid:
        return {
            "profile": result.profile,
            "eligible": False,
            "reason": result.reason,
            "compared": 0,
            "mismatches": 0,
            "parity_ok": None,
        }

    mismatches: list[str] = []
    compared = 0
    for key, candidate in result.values.items():
        # We deliberately cannot verify unknown Estate mode semantics yet.
        if key == "state" and candidate is None:
            continue
        legacy = read_legacy(key)
        compared += 1
        if isinstance(candidate, (float, int)) and type(candidate) is not bool and isinstance(legacy, (float, int)) and type(legacy) is not bool:
            equal = abs(candidate - legacy) < 1e-6
        else:
            equal = legacy == candidate
        if not equal:
            mismatches.append(key)

    return {
        "profile": result.profile,
        "eligible": True,
        "reason": result.reason,
        "compared": compared,
        "mismatches": len(mismatches),
        "mismatch_keys": mismatches[:12],
        "parity_ok": not mismatches,
        "warnings": list(result.warnings),
        # False until independently verified in actual charge and discharge.
        "ready_for_cutover": False,
    }
