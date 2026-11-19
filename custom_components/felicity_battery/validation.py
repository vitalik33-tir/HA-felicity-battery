"""Conservative read-only validation for the independently verified FLA48200-P.

Never infer arbitrary model layouts from one numeric capacity class. Validation
is model-gated and intended to reject physically impossible, malformed
telemetry; measured warning/fault registers remain authoritative. No writes.
"""

from __future__ import annotations
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
    validated. Known fault conditions are *not* interpreted as a bad frame.
    """
    if not isinstance(configured_model, str) or configured_model.strip().upper() != MODEL:
        return ValidationResult(True, "model_unverified_legacy", False)

    def bad(reason: str) -> ValidationResult:
        return ValidationResult(False, reason, True)

    # Never mask a real BMS alarm because a sensor value became invalid
    # during the alarm. Allow raw alarm information to reach existing
    # fault/warning entities, even if some measurement fields are absent.
    for key in ("Bfault", "Bwarn", "BBfault", "BBwarn"):
        value = data.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 65535):
            return bad("invalid_" + key.lower())
    if any(
        type(data.get(key)) is int and data[key] != 0
        for key in ("Bfault", "Bwarn", "BBfault", "BBwarn")
    ):
        return ValidationResult(True, "alarm_frame_preserved", True)

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
    for key in ("Bfault", "Bwarn"):
        value = data.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 65535):
            return bad("invalid_" + key.lower())

    rows = data.get("BatcelList")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        return bad("missing_cell_row")
    if len(rows[0]) != 16:
        return bad("unexpected_cell_count")
    cells = rows[0]
    if any(type(v) is not int or not 1000 <= v <= 5000 for v in cells):
        return bad("invalid_cell_voltage")

    maximum = _get(data, "BMaxMin", 0, 0)
    minimum = _get(data, "BMaxMin", 0, 1)
    if type(maximum) is not int or type(minimum) is not int:
        return bad("missing_extrema")
    if not 1000 <= minimum <= maximum <= 5000:
        return bad("invalid_extrema")
    # Telemetry comes from slightly asynchronous internal channels.
    # Broad thresholds avoid rejecting plausible transient measurements.
    if abs(max(cells) - maximum) > 100 or abs(min(cells) - minimum) > 100:
        return bad("extrema_inconsistent")
    if abs(sum(cells) - voltage) > 2000:
        return bad("pack_sum_inconsistent")
    return ValidationResult(True, "validated_fla48200p", True)
