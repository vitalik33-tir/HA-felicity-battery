"""P2.2 model-scoped invalid/missing telemetry regression tests."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "p2_validation_tests", ROOT / "custom_components/felicity_battery/validation.py"
)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)
SAMPLE = ROOT / "tests/fixtures/fla48200p_standby.json"


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class ModelValidationTests(unittest.TestCase):
    def test_reference_capture_is_valid(self):
        result = validation.validate_runtime(sample(), configured_model="FLA48200-P")
        self.assertTrue(result.valid)
        self.assertTrue(result.checked)
        self.assertEqual(result.reason, "validated_fla48200p")

    def test_no_unverified_model_is_changed(self):
        for model in (None, "", "LUX-X-96050HG01", "FLA48200"):
            result = validation.validate_runtime(
                {"Batsoc": [[999999]], "Batt": []}, configured_model=model
            )
            self.assertTrue(result.valid)
            self.assertFalse(result.checked)

    def test_missing_essential_fields_are_rejected(self):
        for field in ("Type", "SubType", "BMSpara", "Batt", "Batsoc", "BatcelList", "BMaxMin"):
            data = sample()
            data.pop(field)
            result = validation.validate_runtime(data, configured_model="FLA48200-P")
            self.assertFalse(result.valid, field)

    def test_suspicious_voltages_soc_and_cell_sentinels_rejected(self):
        changes = [
            ("Batt", 0, 0, 0),
            ("Batt", 0, 0, 150000),
            ("Batt", 1, 0, None),
            ("Batsoc", 0, 0, 999999),
            ("Batsoc", 0, 0, -1),
            ("BatcelList", 0, 0, 65535),
            ("BatcelList", 0, 0, 0),
            ("BMaxMin", 0, 0, 65535),
        ]
        for field, row, col, value in changes:
            data = sample()
            data[field][row][col] = value
            result = validation.validate_runtime(data, configured_model="FLA48200-P")
            self.assertFalse(result.valid, (field,row,col,value,result.reason))

    def test_missing_cell_and_inconsistent_pack_voltage_rejected(self):
        data = sample()
        data["BatcelList"][0].pop()
        self.assertFalse(validation.validate_runtime(data, configured_model="FLA48200-P").valid)
        data = sample()
        data["Batt"][0][0] -= 5000
        self.assertFalse(validation.validate_runtime(data, configured_model="FLA48200-P").valid)

    def test_small_measurement_offsets_and_state_changes_are_allowed(self):
        data = sample()
        data["Estate"] = 9152
        data["Bstate"] = 9152
        data["Batt"][1][0] = -250
        data["Bfault"] = 4
        data["Bwarn"] = 1
        self.assertTrue(validation.validate_runtime(data, configured_model="FLA48200-P").valid)

    def test_no_warning_field_is_not_forced_to_zero(self):
        data = sample()
        data.pop("Bwarn")
        self.assertTrue(validation.validate_runtime(data, configured_model="FLA48200-P").valid)
        data["Bwarn"] = "N/A"
        self.assertFalse(validation.validate_runtime(data, configured_model="FLA48200-P").valid)

    def test_alarm_frames_are_not_suppressed_by_bad_measurements(self):
        for alarm in ("Bfault", "Bwarn", "BBfault", "BBwarn"):
            data = sample()
            data[alarm] = 4
            data["BatcelList"][0][0] = 65535
            data["Batt"][0][0] = 0
            result = validation.validate_runtime(data, configured_model="FLA48200-P")
            self.assertFalse(result.valid, alarm)
            # An unclean frame is *never* declared physically valid merely
            # because a nonzero hardware alarm is present.
            sanitized = validation.sanitize_runtime(data, configured_model="FLA48200-P")
            self.assertTrue(validation.has_hardware_alarm(sanitized))
            self.assertIsNone(sanitized["BatcelList"])
            self.assertIsNone(sanitized["Batt"][0][0])

    def test_minus_one_current_preserved_as_valid_measurement(self):
        data = sample()
        data["Batt"][1][0] = -1
        self.assertTrue(validation.validate_runtime(data, configured_model="FLA48200-P").valid)


if __name__ == "__main__":
    unittest.main()
