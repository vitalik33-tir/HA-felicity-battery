"""P3.1 display correctness: preserve firmware raw components and entity keys."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest

COMP = Path(__file__).resolve().parents[1] / "custom_components" / "felicity_battery"
spec = importlib.util.spec_from_file_location("p31_helpers", COMP / "helpers.py")
assert spec and spec.loader
h = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = h
spec.loader.exec_module(h)


class FirmwareDisplayTests(unittest.TestCase):
    def test_real_fla48200p_bcu_incomplete_minor_is_not_fabricated(self):
        self.assertEqual(
            h.format_firmware_components(522, 65535), "522 (неполная версия)"
        )

    def test_real_fla48200p_bmu_has_no_confirmed_fw(self):
        self.assertEqual(
            h.format_firmware_components(0, 65535), "Не определена"
        )

    def test_valid_scu_version_is_unchanged(self):
        self.assertEqual(h.format_firmware_components(6, 0), "6-00-00")
        self.assertEqual(h.format_firmware_components(203, 586), "203-05-86")

    def test_absent_firmware_does_not_invent_entity(self):
        self.assertIsNone(h.format_firmware_components(None, None))
        self.assertIsNone(h.format_firmware_components(None, 100))

    def test_invalid_sentinels_never_become_decimal_version(self):
        for major in (-1, 32767, 65535, True, "522"):
            with self.subTest(major=major):
                self.assertEqual(
                    h.format_firmware_components(major, 0), "Не определена"
                )
        for minor in (-1, 32767, 65535, "65535", None, False):
            with self.subTest(minor=minor):
                self.assertEqual(
                    h.format_firmware_components(522, minor),
                    "522 (неполная версия)",
                )

    def test_raw_values_are_still_attributes(self):
        code = (COMP / "sensor.py").read_text(encoding="utf-8")
        self.assertIn('return format_firmware_components(major, minor)', code)
        self.assertIn('return format_firmware_components(', code)
        self.assertIn('basic = data.get("_basic")', code)
        self.assertIn('return basic', code)
        # All original name+unique_id descriptions are independently checked
        # by the P2.5 reference contract test.
        self.assertIn('self._attr_unique_id = f"{entry.entry_id}_{description.key}"', code)


if __name__ == "__main__":
    unittest.main()
