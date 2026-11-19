from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "felicity_battery"
spec = importlib.util.spec_from_file_location("p0_finish_helpers", COMPONENT / "helpers.py")
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helpers
spec.loader.exec_module(helpers)

t0 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return t0 + timedelta(seconds=seconds)


def sustain(f, spread_mv, start_sec, finish_sec, interval=30):
    """Simulate normal 30s BMS polls, including both endpoints."""
    result = None
    for second in range(start_sec, finish_sec + 1, interval):
        result = f.update(spread_mv, at(second))
    return result


class CellDriftConfirmationTests(unittest.TestCase):
    def test_no_immediate_warning_even_above_threshold(self):
        f = helpers.CellDriftConfirmation()
        self.assertFalse(sustain(f, 41, 0, 270))
        self.assertTrue(f.update(41, at(300)))

    def test_activation_at_exactly_30_millivolts(self):
        f = helpers.CellDriftConfirmation()
        self.assertFalse(sustain(f, 30, 0, 270))
        self.assertTrue(f.update(30, at(300)))

    def test_oscillations_reset_pending_activation(self):
        f = helpers.CellDriftConfirmation()
        sustain(f, 39, 0, 150)
        self.assertFalse(f.update(29, at(180)))
        self.assertFalse(sustain(f, 39, 210, 480))
        self.assertTrue(f.update(39, at(510)))

    def test_hysteresis_prevents_flickering(self):
        f = helpers.CellDriftConfirmation()
        self.assertTrue(sustain(f, 41, 0, 300))
        self.assertTrue(sustain(f, 27, 330, 600))
        self.assertTrue(sustain(f, 22, 630, 900))
        self.assertTrue(sustain(f, 21, 930, 1200))
        self.assertTrue(sustain(f, 20, 1230, 1500))
        self.assertFalse(f.update(20, at(1530)))

    def test_hysteresis_reset_on_rebound(self):
        f = helpers.CellDriftConfirmation()
        self.assertTrue(sustain(f, 31, 0, 300))
        self.assertTrue(sustain(f, 19, 330, 450))
        self.assertTrue(f.update(23, at(480)))  # Resets pending clear.
        self.assertTrue(sustain(f, 19, 510, 780))
        self.assertFalse(f.update(19, at(810)))

    def test_missing_data_resets_pending_but_keeps_active_state(self):
        f = helpers.CellDriftConfirmation()
        self.assertFalse(sustain(f, 31, 0, 240))
        self.assertFalse(f.update(None, at(270)))
        self.assertIsNone(f.last_spread_mv)
        self.assertTrue(sustain(f, 31, 300, 600))
        self.assertTrue(sustain(f, 20, 630, 690))
        self.assertTrue(f.update(None, at(720)))
        self.assertTrue(sustain(f, 20, 750, 1020))
        self.assertFalse(f.update(20, at(1050)))

    def test_network_gap_does_not_confirm_alert(self):
        f = helpers.CellDriftConfirmation()
        self.assertFalse(f.update(35, at(0)))
        self.assertFalse(f.update(35, at(300)))  # No polls for five minutes.
        self.assertTrue(sustain(f, 35, 330, 600))

    def test_unknown_does_not_activate(self):
        f = helpers.CellDriftConfirmation()
        self.assertFalse(f.has_valid_sample)
        self.assertFalse(f.update(None, at(0)))
        self.assertFalse(f.update(-1, at(180)))
        self.assertFalse(f.has_valid_sample)

    def test_invalid_thresholds_and_time_rejected(self):
        with self.assertRaises(ValueError):
            helpers.CellDriftConfirmation(on_threshold_mv=30, off_threshold_mv=30)
        with self.assertRaises(ValueError):
            helpers.CellDriftConfirmation(delay_seconds=0)
        with self.assertRaises(ValueError):
            helpers.CellDriftConfirmation(max_sample_gap_seconds=0)
        with self.assertRaises(ValueError):
            helpers.CellDriftConfirmation().update(45, datetime(2026, 11, 19))


class IntegrationContractTests(unittest.TestCase):
    def test_existing_alarm_untouched_new_alarm_separate(self):
        tree = ast.parse((COMPONENT / "binary_sensor.py").read_text())
        keys = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FelicityBinarySensorDescription":
                for keyword in node.keywords:
                    if keyword.arg == "key" and isinstance(keyword.value, ast.Constant):
                        keys[keyword.value.value] = node
        self.assertIn("cell_drift_high", keys)
        self.assertIn("cell_drift_confirmed", keys)
        self.assertEqual(len(keys), 7)

    def test_capacity_hidden_by_default_only_for_new_entries(self):
        tree = ast.parse((COMPONENT / "sensor.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "FelicitySensorDescription":
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            key = keywords.get("key")
            if isinstance(key, ast.Constant) and key.value == "capacity_ah":
                option = keywords.get("entity_registry_visible_default")
                self.assertIsInstance(option, ast.Constant)
                self.assertIs(option.value, False)
                return
        self.fail("Legacy BMS capacity sensor must remain present.")

    def test_battery_state_exposes_raw_hex_and_flags(self):
        source = (COMPONENT / "sensor.py").read_text()
        self.assertIn('"estate_raw": raw', source)
        self.assertIn('"estate_hex": f"0x{raw:04X}"', source)
        self.assertIn('"mode_from_bits":', source)
        self.assertIn('"unclassified_lower_bits_hex":', source)


if __name__ == "__main__":
    unittest.main()
