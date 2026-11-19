"""P2.5 regression guard against the verified stable main entity contract.

Fixture is a *static*, identifying-data-free metadata snapshot of commit
a547418. Tests deliberately allow only documented P0 metadata changes:
capacity label/visibility and precision 3 for sixteen cell voltages.
No HA installation, network or Git checkout required to run this test.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "felicity_battery"
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "p25_main_entity_contract.json").read_text(
        encoding="utf-8"
    )
)


def descriptions(file: Path, classname: str) -> dict[str, dict[str, str]]:
    source = ast.parse(file.read_text(encoding="utf-8"))
    result: dict[str, dict[str, str]] = {}
    count = 0
    for n in ast.walk(source):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Name):
            continue
        if n.func.id != classname:
            continue
        metadata = {kw.arg: ast.unparse(kw.value) for kw in n.keywords}
        key_value = n.keywords[0].value if n.keywords and n.keywords[0].arg == "key" else None
        if key_value is None:
            key_value = next((kw.value for kw in n.keywords if kw.arg == "key"), None)
        if not isinstance(key_value, ast.Constant) or not isinstance(key_value.value, str):
            raise AssertionError("Every entity must have a fixed string key")
        key = key_value.value
        count += 1
        if key in result:
            raise AssertionError(f"Duplicate entity key: {key}")
        result[key] = metadata
    if count != len(result):
        raise AssertionError("Duplicate entity descriptions")
    return result


class OriginalEntityContractTests(unittest.TestCase):
    def test_contract_comes_from_verified_stable_main(self):
        self.assertEqual(FIXTURE["main_commit"], "a547418d924665945f0f924908f21407263f541e")
        self.assertEqual(len(FIXTURE["sensor"]), 75)
        self.assertEqual(len(FIXTURE["binary_sensor"]), 6)

    def test_original_descriptions_are_preserved(self):
        for filename, classname in (
            ("sensor", "FelicitySensorDescription"),
            ("binary_sensor", "FelicityBinarySensorDescription"),
        ):
            legacy = FIXTURE[filename]
            current = descriptions(COMP / f"{filename}.py", classname)
            with self.subTest(kind=filename):
                self.assertFalse(set(legacy) - set(current), "Legacy keys were removed")
            for key, fields in legacy.items():
                actual = current[key]
                with self.subTest(kind=filename, key=key):
                    for name, value in fields.items():
                        # Documented, intentionally non-regressive P0 changes.
                        if filename == "sensor" and key == "capacity_ah" and name == "name":
                            self.assertEqual(actual[name], "'BMS Capacity Class'")
                            continue
                        if (
                            filename == "sensor"
                            and key.startswith("cell_")
                            and key.endswith("_v")
                            and name == "suggested_display_precision"
                        ):
                            self.assertEqual(actual[name], "3")
                            continue
                        self.assertEqual(actual.get(name), value, f"{key}/{name} changed")
                    additions = set(actual) - set(fields)
                    if filename == "sensor" and key == "capacity_ah":
                        self.assertEqual(additions, {"entity_registry_visible_default"})
                        self.assertEqual(actual["entity_registry_visible_default"], "False")
                    else:
                        self.assertFalse(additions, f"Undocumented additions: {key} / {additions}")

    def test_additions_have_stable_keys_and_diagnostic_visibility(self):
        sensors = descriptions(COMP / "sensor.py", "FelicitySensorDescription")
        binary = descriptions(COMP / "binary_sensor.py", "FelicityBinarySensorDescription")
        self.assertEqual(
            set(sensors) - set(FIXTURE["sensor"]),
            {"rated_capacity_ah", "max_cell_index", "min_cell_index", "bstate_raw"},
        )
        self.assertEqual(
            set(binary) - set(FIXTURE["binary_sensor"]),
            {"cell_drift_confirmed"},
        )
        self.assertEqual(sensors["bstate_raw"]["entity_registry_visible_default"], "False")

    def test_unique_id_contract_and_existing_keys_not_renamed(self):
        for kind, class_name in (
            ("sensor", "FelicitySensor"),
            ("binary_sensor", "FelicityBinarySensor"),
        ):
            code = (COMP / f"{kind}.py").read_text(encoding="utf-8")
            self.assertIn('self._attr_unique_id = f"{entry.entry_id}_{description.key}"', code)
            self.assertIn(f"class {class_name}(", code)


if __name__ == "__main__":
    unittest.main()
