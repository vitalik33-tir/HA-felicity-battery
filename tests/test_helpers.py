from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "felicity_battery_helpers_test",
    ROOT / "custom_components/felicity_battery/helpers.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load helpers.py")
helpers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helpers
SPEC.loader.exec_module(helpers)


class BatteryPackCountTests(unittest.TestCase):
    def test_prefers_official_bms_count(self) -> None:
        data = {
            "BMSpara": [[3]],
            "BatcelList": [[5330, 5320], [3331, 3330]],
        }

        self.assertEqual(helpers.battery_pack_count(data), 3)

    def test_counts_populated_pack_voltage_slots_for_multi_array_device(self) -> None:
        data = {
            "BatcelList": [[5330, 5320, 5310, 0, 65535], [3331, 3330]],
        }

        self.assertEqual(helpers.battery_pack_count(data), 3)

    def test_single_cell_array_is_one_module_not_sixteen(self) -> None:
        data = {"BatcelList": [[3331] * 16]}

        self.assertEqual(helpers.battery_pack_count(data), 1)


    def test_placeholder_second_row_is_one_module(self) -> None:
        data = {"BatcelList": [[3309] * 16, [65535] * 16]}

        self.assertEqual(helpers.battery_pack_count(data), 1)


# Issue #4: LUX-Y-48300 sends a second BatcelList row filled with 65535.
SINGLE_MODULE_WITH_PLACEHOLDER_ROW = {
    "BatcelList": [
        [3309, 3310, 3309, 3311, 3310, 3311, 3309, 3311,
         3309, 3310, 3310, 3312, 3309, 3311, 3310, 3311],
        [65535] * 16,
    ],
    "BMaxMin": [[3312, 3309], [11, 0]],
}

# LUX-X-96050HG01 (modular): row 0 = pack voltages (/100), row 1 = cells.
MODULAR_PACK = {
    "BatcelList": [[5330, 5320, 5310], [3331, 3330, 3345, 3338]],
    "BMaxMin": [[3345, 3338], [6, 7]],
}


class BatcelLayoutTests(unittest.TestCase):
    def test_placeholder_second_row_means_row_zero_is_cells(self) -> None:
        data = SINGLE_MODULE_WITH_PLACEHOLDER_ROW

        self.assertFalse(helpers.batcel_has_pack_row(data))
        self.assertEqual(helpers.batcel_cell_row(data), data["BatcelList"][0])

    def test_modular_pack_has_pack_row_and_cells_in_row_one(self) -> None:
        data = MODULAR_PACK

        self.assertTrue(helpers.batcel_has_pack_row(data))
        self.assertEqual(helpers.batcel_cell_row(data), data["BatcelList"][1])

    def test_modular_pack_detected_without_bmaxmin(self) -> None:
        data = {"BatcelList": MODULAR_PACK["BatcelList"]}

        self.assertTrue(helpers.batcel_has_pack_row(data))

    def test_modular_pack_stays_pack_when_row_one_glitches(self) -> None:
        data = {
            "BatcelList": [[5330, 5320, 5310], [65535] * 4],
            "BMaxMin": MODULAR_PACK["BMaxMin"],
        }

        self.assertTrue(helpers.batcel_has_pack_row(data))

    def test_single_array_is_cells(self) -> None:
        data = {"BatcelList": [[3331] * 16]}

        self.assertFalse(helpers.batcel_has_pack_row(data))
        self.assertEqual(helpers.batcel_cell_row(data), [3331] * 16)

    def test_missing_list_has_no_cell_row(self) -> None:
        self.assertFalse(helpers.batcel_has_pack_row({}))
        self.assertIsNone(helpers.batcel_cell_row({}))


class BatteryStateTests(unittest.TestCase):
    def test_decodes_known_estate_values(self) -> None:
        self.assertEqual(helpers.battery_state(320), "full")
        self.assertEqual(helpers.battery_state(960), "standby")
        self.assertEqual(helpers.battery_state(9152), "charging")
        self.assertEqual(helpers.battery_state(5056), "discharging")

    def test_preserves_unknown_estate_code(self) -> None:
        self.assertEqual(helpers.battery_state(1234), "unknown(1234)")

    def test_missing_estate_is_unavailable(self) -> None:
        self.assertIsNone(helpers.battery_state(None))


if __name__ == "__main__":
    unittest.main()
