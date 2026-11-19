"""Conservative read-only validation for the independently verified FLA48200-P.

Never infer arbitrary model layouts from one numeric capacity class. Validation
is model-gated and intended to reject physically impossible, malformed
telemetry; measured warning/fault registers remain authoritative. No writes.
"""

from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

MODEL = "FLA48200-P"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str
    checked: bool


def _get(data: Mapping[str, Any], key: str, row: int, col: int) -> Any:
    try:
        return data[key][row][col]
    except (TypeError, KeyError, IndexError):
        return None


def validate_runtime(
    data: Mapping[str, Any], *, configured_model: str | None
) -> ValidationResult:
    """Apply conservative FLA48200-P checks; do not alter the payload.

    Only explicit model choice enables this validator. For other models the
    legacy parser stays authoritative until their individual profiles are
    validated. Physical measurements remain strictly validated even when a
    hardware fault register is nonzero. The caller may separately publish
    a partial sanitized alarm frame, never unverified measurement values.
    """
    if not isinstance(configured_model, str) or configured_model.strip().upper() != MODEL:
        return ValidationResult(True, "model_unverified_legacy", False)

    def bad(reason: str) -> ValidationResult:
        return ValidationResult(False, reason, True)

    # Alarm codes are validated independently, but a nonzero code must
    # never bypass physical-value validation here. An explicit sanitation
    # step preserves alarms separately from damaged measurements.
    for key in ("Bfault", "Bwarn", "BBfault", "BBwarn"):
        value = data.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 65535):
            return bad("invalid_" + key.lower())
    if data.get("Type") != 112 or data.get("SubType") != 7250:
        return bad("type_or_subtype_mismatch")

    if _get(data, "BMSpara", 0, 0) != 1:
        return bad("single_pack_layout_missing")

    voltage = _get(data, "Batt", 0, 0)
    current = _get(data, "Batt", 1, 0)
    soc = _get(data, "Batsoc", 0, 0)
    if type(voltage) is not int or not 15000 <= voltage <= 100000:
        return bad("invalid_pack_voltage")
    if type(current) is not int or not -32000 <= current <= 32000:
        return bad("invalid_pack_current")
    if type(soc) is not int or not 0 <= soc <= 10000:
        return bad("invalid_soc")
    # Do not reinterpret -1 as missing: it really means -0.1A in Batt.
    rows = data.get("BatcelList")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        return bad("missing_cell_row")
    if len(rows[0]) != 16:
        return bad("unexpected_cell_count")
    cells = rows[0]
    if any(type(v) is not int or not 2000 <= v <= 4500 for v in cells):
        return bad("invalid_cell_voltage")

    maximum = _get(data, "BMaxMin", 0, 0)
    minimum = _get(data, "BMaxMin", 0, 1)
    if type(maximum) is not int or type(minimum) is not int:
        return bad("missing_extrema")
    if not 2000 <= minimum <= maximum <= 4500:
        return bad("invalid_extrema")
    # Telemetry comes from slightly asynchronous internal channels.
    # Broad thresholds avoid rejecting plausible transient measurements.
    if abs(max(cells) - maximum) > 100 or abs(min(cells) - minimum) > 100:
        return bad("extrema_inconsistent")
    if abs(sum(cells) - voltage) > 800:
        return bad("pack_sum_inconsistent")
    return ValidationResult(True, "validated_fla48200p", True)



def has_hardware_alarm(data: Mapping[str, Any]) -> bool:
    """A nonzero hardware alarm with valid numeric register representation."""
    return any(
        type(data.get(key)) is int and 0 < data[key] <= 65535
        for key in ("Bfault", "Bwarn", "BBfault", "BBwarn")
    )


def sanitize_runtime(
    data: dict[str, Any], *, configured_model: str | None
) -> dict[str, Any]:
    """Detach and neutralize invalid readings, never discard raw alarm codes.

    This is *only* enabled for an explicitly named FLA48200-P. On ordinary
    frames, the separate validate_runtime() still rejects a broken core
    voltage/SOC/cell array and triggers a new TCP reading. On an alarm
    frame, the alarm registers survive even if readings are malformed,
    while invalid physical values become None (HA unknown), not zero.

    The input dict and all nested lists are left unchanged.
    """
    if not isinstance(configured_model, str) or configured_model.strip().upper() != MODEL:
        return data

    out = deepcopy(data)

    def in_range(value: Any, low: int, high: int) -> bool:
        return type(value) is int and low <= value <= high

    def mask(key: str, row: int, col: int, low: int, high: int) -> None:
        """Mask one physical reading while retaining neighboring channels."""
        group = out.get(key)
        if not isinstance(group, list) or row >= len(group):
            return
        if not isinstance(group[row], list):
            out[key] = None
            return
        if col < len(group[row]) and not in_range(group[row][col], low, high):
            group[row][col] = None

    # Never reinterpret a malformed alarm code as a normal zero reading.
    for key in ("Bfault", "Bwarn", "BBfault", "BBwarn"):
        value = out.get(key)
        if value is not None and not in_range(value, 0, 65535):
            out[key] = None

    # Identity mismatch means we cannot trust *any* numerical channel.
    # Raw alarm registers remain available for the existing alarm sensors.
    if out.get("Type") != 112 or out.get("SubType") != 7250 or _get(out, "BMSpara", 0, 0) != 1:
        for key in (
            "Batt", "Batsoc", "BatcelList", "BMaxMin", "BTemp",
            "BtemList", "LVolCur", "BattList", "BatsocList",
            "Estate", "Bstate", "workM",
        ):
            if key in out:
                out[key] = None
        return out

    # Mandatory channels: a broken ordinary frame remains invalid and
    # causes retry; an alarm frame is allowed only with these fields
    # individually unknown. -1 is a *valid* -0.1 A on Batt[1][0].
    mask("Batt", 0, 0, 15000, 100000)
    mask("Batt", 1, 0, -32000, 32000)
    mask("Batsoc", 0, 0, 0, 10000)

    # Optional measurements never invalidate an otherwise usable frame.
    # SOH is reported in tenths of percent; capacity class in mAh raw.
    mask("Batsoc", 0, 1, 0, 1000)
    mask("Batsoc", 0, 2, 1, 1000000)

    # BTemp[0] is the only verified thermal row shown by existing sensors;
    # BTemp[1] has a *different, unverified* meaning and is not re-scaled.
    mask("BTemp", 0, 0, -400, 1000)
    mask("BTemp", 0, 1, -400, 1000)

    # Runtime limits are optional; 0 A is a legitimate reported limit.
    for index in (0, 1):
        mask("LVolCur", 0, index, 150, 1000)
        mask("LVolCur", 1, index, 0, 32000)

    for key in ("Estate", "Bstate", "workM"):
        value = out.get(key)
        if value is not None and not in_range(value, 0, 65535):
            out[key] = None

    # Reject the entire *cell measurement group* if any slot is corrupt,
    # or if the reported extrema/sum make the physical row ambiguous.
    rows = out.get("BatcelList")
    cells = (
        rows[0]
        if isinstance(rows, list) and rows and isinstance(rows[0], list)
        else None
    )
    good_cells = (
        isinstance(cells, list)
        and len(cells) == 16
        and all(in_range(value, 2000, 4500) for value in cells)
    )
    if not good_cells:
        out["BatcelList"] = None
        out["BMaxMin"] = None
    else:
        max_mv, min_mv = _get(out, "BMaxMin", 0, 0), _get(out, "BMaxMin", 0, 1)
        good_extrema = (
            in_range(min_mv, 2000, 4500)
            and in_range(max_mv, 2000, 4500)
            and min_mv <= max_mv
            and abs(max(cells) - max_mv) <= 100
            and abs(min(cells) - min_mv) <= 100
        )
        if not good_extrema:
            out["BMaxMin"] = None
        pack_mv = _get(out, "Batt", 0, 0)
        if in_range(pack_mv, 15000, 100000) and abs(sum(cells) - pack_mv) > 800:
            out["BatcelList"] = None
            out["BMaxMin"] = None

    return out
