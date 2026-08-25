from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {relative_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load_module(
    "felicity_battery_api_test",
    "custom_components/felicity_battery/api.py",
)


class FelicityPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = api.FelicityClient("127.0.0.1", 53970)

    def test_normalizes_none_without_changing_string_value(self) -> None:
        objects = api.parse_json_objects("{'missing': None, 'label': 'None'}")

        self.assertEqual(objects, [{"missing": None, "label": "None"}])

    def test_extracts_concatenated_objects_with_braces_in_string(self) -> None:
        objects = api.parse_json_objects(
            "{'first': '{ok}', 'value': None}{'second': 2}"
        )

        self.assertEqual(
            objects,
            [{"first": "{ok}", "value": None}, {"second": 2}],
        )

    def test_parses_multi_battery_payload_and_missing_fields(self) -> None:
        data = self.client._parse_real_payload(
            "{'Batt': [[53300], [0], [None]], "
            "'Batsoc': [[0, 1000, 250000]], 'workM': 1, "
            "'BMSpara': [[3]], "
            "'BatcelList': [[5330, 5320, 5310], [3331, 3330], [9, 8]]}"
        )

        self.assertEqual(data["workM"], 1)
        self.assertEqual(data["BMSpara"], [[3]])
        self.assertEqual(len(data["BatcelList"]), 3)
        self.assertEqual(data["Batt"][1][0], 0)
        self.assertEqual(data["Batsoc"][0][0], 0)

    def test_rejects_zero_voltage_disconnect_payload(self) -> None:
        with self.assertRaisesRegex(api.FelicityApiError, "zero battery voltage"):
            self.client._parse_real_payload(
                "{'Batt': [[0], [0], [None]], "
                "'Batsoc': [[0, 1000, 250000]], 'Bfault': 0, 'Bwarn': 0}"
            )

    def test_regex_fallback_preserves_multi_battery_fields(self) -> None:
        data = self.client._parse_real_payload(
            '{"Batt": [[53300], [0], [null]], '
            '"Batsoc": [[5000, 1000, 250000]], '
            '"BMSpara": [[2]], "workM": 4, broken}'
        )

        self.assertEqual(data["BMSpara"], [[2]])
        self.assertEqual(data["workM"], 4)

    def test_best_effort_payloads_recover_none_and_concatenated_json(self) -> None:
        self.client._async_read_raw_with_retry = AsyncMock(
            side_effect=[
                "{'Batt': [[53300], [0], [None]], 'Batsoc': [[5000, 1000, 250000]]}",
                "{'Type': 1, 'version': None}",
                "{'wCVP80': None}{'cVolHi': 3650}",
                "{'rssi': None, 'tick': 10}",
            ]
        )

        data = asyncio.run(self.client.async_get_data())

        self.assertIsNone(data["_basic"]["version"])
        self.assertEqual(data["_settings"]["cVolHi"], 3650)
        self.assertIsNone(data["_settings"]["wCVP80"])
        self.assertIsNone(data["_date"]["rssi"])


if __name__ == "__main__":
    unittest.main()
