from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SENSOR_PATH = ROOT / "custom_components/felicity_battery/sensor.py"


class LegacySensorContractTests(unittest.TestCase):
    def test_raw_bms_firmware_entities_keep_their_original_keys_and_names(self) -> None:
        source = SENSOR_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        descriptions: dict[str, str] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "FelicitySensorDescription":
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            key = keywords.get("key")
            name = keywords.get("name")
            if isinstance(key, ast.Constant) and isinstance(name, ast.Constant):
                descriptions[key.value] = name.value

        self.assertEqual(descriptions["bms_m1_fw"], "Battery BMS M1 FW")
        self.assertEqual(descriptions["bms_m2_fw"], "Battery BMS M2 FW")
        self.assertIn('return basic.get("M1SwVer")', source)
        self.assertIn('return basic.get("M2SwVer")', source)


if __name__ == "__main__":
    unittest.main()
