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


if __name__ == "__main__":
    unittest.main()
