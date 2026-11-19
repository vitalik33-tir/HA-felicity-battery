"""P0 regression tests: FLA48200-P plus the previously supported multi-pack format."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for module_name, relative in (
    ("p0_helpers", "custom_components/felicity_battery/helpers.py"),
    ("p0_api", "custom_components/felicity_battery/api.py"),
):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

helpers = sys.modules["p0_helpers"]
api = sys.modules["p0_api"]

# Captured from our installed HA battery on 2026-11-19. Device serial not included.
FLA48200_CELL_MV = [
    3573, 3590, 3599, 3593, 3600, 3597, 3601, 3601,
    3600, 3599, 3595, 3599, 3602, 3600, 3600, 3601,
]


class TestBatteryP0(unittest.TestCase):
    def test_fla_two_rows_row_zero_is_real_16_cells(self):
        cells = FLA48200_CELL_MV
        snapshot = {
            "Batt": [[sum(cells)], [0]],
            "Batsoc": [[10000, 1000, 250000]],
            "BatcelList": [cells, [0] * 16],
            "BMaxMin": [[3602, 3573], [12, 0]],
            "BMSpara": [[1]],
        }
        self.assertEqual(helpers.cell_voltage_row(snapshot), 0)
        self.assertTrue(helpers.primary_row_is_cells(snapshot))
        self.assertEqual(helpers.cell_voltage_mv(snapshot, 0), 3573)
        self.assertEqual(helpers.cell_voltage_mv(snapshot, 15), 3601)
        self.assertEqual(helpers.cell_extrema_indices(snapshot), (13, 1))
        self.assertEqual(helpers.battery_pack_count(snapshot), 1)
        self.assertAlmostEqual(sum(cells) / 1000, 57.549, delta=0.2)

    def test_multi_pack_row_zero_still_pack_voltages(self):
        row1 = [3330] * 16
        row1[0] = 3325
        row1[10] = 3338
        snapshot = {
            "Batt": [[53300], [0]],
            "BatcelList": [[5330, 5320, 5310, 0, 65535], row1],
            "BMaxMin": [[3338, 3325], [10, 0]],
        }
        self.assertEqual(helpers.cell_voltage_row(snapshot), 1)
        self.assertFalse(helpers.primary_row_is_cells(snapshot))
        self.assertEqual(helpers.cell_voltage_mv(snapshot, 10), 3338)
        self.assertEqual(helpers.cell_extrema_indices(snapshot), (11, 1))
        self.assertEqual(helpers.battery_pack_count(snapshot), 3)

    def test_invalid_bms_cell_indices_fall_back_to_measured_positions(self):
        snapshot = {
            "Batt": [[sum(FLA48200_CELL_MV)], [0]],
            "BatcelList": [FLA48200_CELL_MV],
            "BMaxMin": [[3602, 3573], [0, 12]],
        }
        self.assertEqual(helpers.cell_extrema_indices(snapshot), (13, 1))

    def test_missing_cell_does_not_shift_position(self):
        cells = [3330] * 16
        cells[4] = 0
        cells[15] = 3350
        cells[0] = 3310
        snapshot = {
            "Batt": [[sum(cells) - 0], [0]],
            "BatcelList": [cells],
            "BMaxMin": [[3350, 3310], [15, 0]],
        }
        self.assertEqual(helpers.cell_voltage_row(snapshot), 0)
        self.assertEqual(helpers.cell_voltage_mv(snapshot, 4), None)
        self.assertEqual(helpers.cell_extrema_indices(snapshot), (16, 1))

    def test_unverified_layout_not_classified_as_cells(self):
        snapshot = {
            "Batt": [[56000], [0]],
            "BatcelList": [[5320, 5330, 0]],
            "BMaxMin": [[3600, 3500], [0, 1]],
        }
        self.assertIsNone(helpers.cell_voltage_row(snapshot))
        self.assertEqual(helpers.cell_extrema_indices(snapshot), (None, None))

    def test_missing_bwarn_not_coerced_to_clear(self):
        client = api.FelicityClient("127.0.0.1", 53970)
        payload = client._parse_real_payload(
            '{"Batt": [[56000], [0], [null]], '
            '"Batsoc": [[10000, 1000, 250000]], "Bfault": 0}'
        )
        self.assertIsNone(payload.get("Bwarn"))

    def test_explicit_zero_bwarn_preserved(self):
        client = api.FelicityClient("127.0.0.1", 53970)
        payload = client._parse_real_payload(
            '{"Batt": [[56000], [0], [null]], '
            '"Batsoc": [[10000, 1000, 250000]], "Bwarn": 0}'
        )
        self.assertEqual(payload.get("Bwarn"), 0)

    def test_sixteen_primary_cell_entities_show_millivolt_precision(self):
        import ast
        source = (ROOT / "custom_components/felicity_battery/sensor.py").read_text()
        cells = {}
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "FelicitySensorDescription":
                continue
            options = {kw.arg: kw.value for kw in node.keywords}
            key_node = options.get("key")
            if not isinstance(key_node, ast.Constant):
                continue
            if key_node.value in {f"cell_{i}_v" for i in range(1, 17)}:
                precision = options.get("suggested_display_precision")
                cells[key_node.value] = precision.value if isinstance(precision, ast.Constant) else None
        self.assertEqual(cells, {f"cell_{i}_v": 3 for i in range(1, 17)})

    def test_existing_entity_keys_not_dropped(self):
        import ast
        import subprocess
        path = "custom_components/felicity_battery/sensor.py"
        baseline = subprocess.check_output(
            ["git", "show", "a547418:" + path], cwd=ROOT, text=True
        )
        updated = (ROOT / path).read_text(encoding="utf-8")

        def keys(source):
            return {
                k.value
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "FelicitySensorDescription"
                for kw in node.keywords
                if kw.arg == "key"
                for k in [kw.value]
                if isinstance(k, ast.Constant)
            }

        self.assertFalse(keys(baseline) - keys(updated))
        self.assertEqual(keys(updated) - keys(baseline),
                         {"rated_capacity_ah", "max_cell_index", "min_cell_index", "bstate_raw"})
        self.assertIn('self._attr_unique_id = f"{entry.entry_id}_{description.key}"',
                      updated)


if __name__ == "__main__":
    unittest.main()
