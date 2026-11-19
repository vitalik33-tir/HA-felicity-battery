"""P1 shadow decoder tests: snapshot-based, model-gated, no HA dependency."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "custom_components" / "felicity_battery" / "profiles.py"
FIXTURE = ROOT / "tests" / "fixtures" / "fla48200p_standby.json"
spec = importlib.util.spec_from_file_location("p1_profiles", SRC)
assert spec is not None and spec.loader is not None
profiles = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = profiles
spec.loader.exec_module(profiles)


def sample() -> dict:
    """Sanitized real 2026-11-19 FLA48200-P standby frame."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class P1ShadowProfileTests(unittest.TestCase):
    def test_standby_fixture_parsed_without_guessing_current(self):
        frame = sample()
        self.assertNotIn("DevSN", frame)
        self.assertNotIn("wifiSN", frame)
        p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertTrue(p.valid, p.reason)
        self.assertEqual(p.profile, "FLA48200-P")
        self.assertEqual(p.values["voltage"], 57.5)
        self.assertEqual(p.values["current"], frame["Batt"][1][0] / 10)
        self.assertEqual(p.values["state"], "Ожидание")
        self.assertEqual(p.values["soc"], 100.0)
        self.assertEqual(p.values["capacity_ah"], 250.0)
        self.assertEqual(p.values["ttl_pack"], 1)
        self.assertEqual(p.values["cell_drift"],
                         round((frame["BMaxMin"][0][0] -
                                frame["BMaxMin"][0][1]) / 1000, 3))
        self.assertEqual(len([k for k in p.values if k.startswith("cell_") and k.endswith("_v")]), 16)
        self.assertEqual(p.values["cell_1_v"], frame["BatcelList"][0][0] / 1000)
        self.assertEqual(p.values["cell_16_v"], frame["BatcelList"][0][15] / 1000)
        self.assertEqual(p.warnings, ())

    def test_minus_one_is_valid_current_on_this_model(self):
        frame = sample()
        frame["Batt"][1][0] = -1
        frame["BattList"][1][0] = -1  # Sentinel on this field, NOT on Batt.
        p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertEqual(p.values["current"], -0.1)
        self.assertEqual(p.values["discharge_current"], 0.1)
        self.assertEqual(p.values["direction"], "discharging")

    def test_missing_or_ambiguous_model_falls_back(self):
        frame = sample()
        for name in (None, "", "Felicity FLA48200", "LUX-E-48250LG03"):
            p = profiles.decode_fla48200p(frame, configured_model=name)
            self.assertFalse(p.valid)
            self.assertEqual(p.reason, "model_not_explicitly_configured")
        self.assertTrue(profiles.decode_fla48200p(frame, configured_model="fla48200-p").valid)

    def test_type_and_capacity_class_are_not_a_model_identifier(self):
        frame = sample()
        frame["SubType"] = 7100
        self.assertFalse(profiles.decode_fla48200p(frame, configured_model="FLA48200-P").valid)
        frame = sample()
        frame["Type"] = 999
        self.assertFalse(profiles.decode_fla48200p(frame, configured_model="FLA48200-P").valid)

    def test_multiple_physical_modules_are_not_fla_single_pack(self):
        frame = sample()
        frame["BMSpara"][0][0] = 2
        p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertFalse(p.valid)
        self.assertEqual(p.reason, "not_a_verified_single_pack")

    def test_missing_cell_payload_or_impossible_units_fails_closed(self):
        for mutation in ("zero", "short", "wrong_scale", "bad_minmax", "sum"):
            frame = sample()
            if mutation == "zero":
                frame["BatcelList"][0][0] = 0
            if mutation == "short":
                frame["BatcelList"][0].pop()
            if mutation == "wrong_scale":
                frame["BatcelList"][0] = [53_000] * 16
            if mutation == "bad_minmax":
                frame["BMaxMin"][0][0] = 4500
            if mutation == "sum":
                frame["Batt"][0][0] = 50000
            p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
            self.assertFalse(p.valid, mutation)

    def test_bstate_undecoded_and_unknown_mode_not_promoted(self):
        frame = sample()
        frame["Bstate"] = 9152
        frame["Estate"] = 832
        p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertTrue(p.valid)
        self.assertIn("bstate_estate_mismatch", p.warnings)
        self.assertEqual(p.values["state"], "Ожидание")
        frame["Estate"] = 9152
        p2 = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertTrue(p2.valid)
        self.assertIsNone(p2.values["state"])
        self.assertIn("estate_outside_verified_standby", p2.warnings)

    def test_missing_warning_is_not_no_alarm(self):
        frame = sample()
        frame.pop("Bwarn")
        p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
        self.assertIsNone(p.values["warning"])

    def test_shadow_comparison_detects_and_reports_only_differences(self):
        p = profiles.decode_fla48200p(sample(), configured_model="FLA48200-P")
        r = profiles.compare_with_legacy(p, lambda k: p.values[k])
        self.assertTrue(r["parity_ok"])
        self.assertEqual(r["compared"], len(p.values))
        self.assertFalse(r["ready_for_cutover"])

        r = profiles.compare_with_legacy(p, lambda k: 0 if k == "current" else p.values[k])
        self.assertFalse(r["parity_ok"])
        self.assertEqual(r["mismatches"], 1)
        self.assertEqual(r["mismatch_keys"], ["current"])

    def test_invalid_profile_never_compares_or_cutovers(self):
        p = profiles.decode_fla48200p(sample(), configured_model="OTHER")
        r = profiles.compare_with_legacy(p, lambda key: self.fail("Unexpected callback"))
        self.assertIsNone(r["parity_ok"])
        self.assertFalse(r["eligible"])

    def test_positive_and_negative_test_currents_only_for_legacy_parity(self):
        frame = sample()
        for raw, direction, charge, discharge in (
            (0, "idle", 0.0, 0.0),
            (16, "charging", 1.6, 0.0),
            (-16, "discharging", 0.0, 1.6),
        ):
            frame["Batt"][1][0] = raw
            p = profiles.decode_fla48200p(frame, configured_model="FLA48200-P")
            self.assertEqual(p.values["direction"], direction)
            self.assertEqual(p.values["charge_current"], charge)
            self.assertEqual(p.values["discharge_current"], discharge)
            if raw != 0:
                self.assertIn("current_under_load_not_yet_verified", p.warnings)


if __name__ == "__main__":
    unittest.main()
