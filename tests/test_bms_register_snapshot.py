"""Read-only BMS register diagnostics: never interpret unverified modes."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest

COMP = Path(__file__).resolve().parents[1] / 'custom_components' / 'felicity_battery'
spec = importlib.util.spec_from_file_location('bms_snapshot_test', COMP / 'helpers.py')
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class RawBmsSnapshotTests(unittest.TestCase):
    def test_verified_idle_832_preserves_two_different_current_fields(self):
        source = {'Bstate': 832, 'Estate': 832,
                  'Batt': [[57500], [-3], [None]],
                  'BattList': [[57450, 65535], [-1, -1]]}
        value = mod.bms_state_snapshot(source)
        self.assertEqual(value['bstate_raw'], 832)
        self.assertEqual(value['bstate_hex'], '0x0340')
        self.assertEqual(value['estate_raw'], 832)
        self.assertTrue(value['bstate_equals_estate'])
        self.assertIs(value['bstate_bit_12'], False)
        self.assertIs(value['bstate_bit_13'], False)
        self.assertEqual(value['batt_current_raw'], -3)
        self.assertEqual(value['battlist_current_raw'], -1)
        self.assertEqual(value['batt_voltage_raw'], 57500)
        self.assertEqual(value['battlist_voltage_raw'], 57450)

    def test_unknown_register_is_not_reported_as_zero_or_standby(self):
        for raw in (None, 65535, 32767, -1, '832', True):
            value = mod.bms_state_snapshot({'Bstate': raw, 'Estate': 832})
            self.assertIsNone(value['bstate_raw'])
            self.assertIsNone(value['bstate_hex'])
            self.assertIsNone(value['bstate_equals_estate'])
            self.assertIsNone(value['bstate_bit_13'])
        value = mod.bms_state_snapshot({})
        self.assertIsNone(value['estate_raw'])
        self.assertIsNone(value['batt_current_raw'])

    def test_mismatched_registers_detected_without_changing_old_state(self):
        value = mod.bms_state_snapshot({'Bstate': 9152, 'Estate': 832})
        self.assertFalse(value['bstate_equals_estate'])
        self.assertTrue(value['bstate_bit_13'])
        self.assertFalse(value['bstate_bit_12'])
        self.assertEqual(mod.battery_state(832), 'Ожидание')

    def test_existing_sensor_names_and_descriptions_untouched(self):
        src = (COMP / 'sensor.py').read_text()
        tree = ast.parse(src)
        keys = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != 'FelicitySensorDescription':
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            if isinstance(kwargs.get('key'), ast.Constant):
                keys[kwargs['key'].value] = kwargs
        self.assertIn('state', keys)
        self.assertIn('current', keys)
        self.assertIn('bstate_raw', keys)
        self.assertIs(keys['bstate_raw']['entity_registry_visible_default'].value, False)
        self.assertIn('return battery_state(data.get("Estate"))', src)
        self.assertIn('return bms_state_snapshot(data)["bstate_raw"]', src)


if __name__ == '__main__':
    unittest.main()
