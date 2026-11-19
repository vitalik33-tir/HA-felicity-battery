"""P3.2: keep model/transport documentation tied to actual read-only source.

These tests assert code/fixture contracts, not that a model's physics has
been validated outside the observed standby regime.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "felicity_battery"
PROTO = (ROOT / "docs" / "P3_2_LOCAL_TCP_PROTOCOL.md").read_text(encoding="utf8")
MODELS = (ROOT / "docs" / "P3_2_MODEL_EVIDENCE.md").read_text(encoding="utf8")
README = (ROOT / "README.md").read_text(encoding="utf8")
API_SOURCE = (COMP / "api.py").read_text(encoding="utf8")
PROFILE_SOURCE = (COMP / "profiles.py").read_text(encoding="utf8")
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "fla48200p_standby.json").read_text(encoding="utf8"))


class ProtocolDocumentationTests(unittest.TestCase):
    def test_commands_match_read_only_client(self):
        exact = (
            "wifilocalMonitor:get dev real infor",
            "wifilocalMonitor:get dev basice infor",
            "wifilocalMonitor:get dev set infor",
            "wifilocalMonitor:get Date",
        )
        for command in exact:
            with self.subTest(command=command):
                self.assertIn(f'b"{command}"', API_SOURCE)
                self.assertIn(command, PROTO)
        self.assertIn("53970", PROTO)
        self.assertNotIn('wifilocalMonitor:set', API_SOURCE)

    def test_documented_timeouts_are_current_constants(self):
        tree = ast.parse(API_SOURCE)
        constants = {
            name.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for name in node.targets if isinstance(name, ast.Name)
        }
        self.assertEqual(constants["MAX_POLL_SECONDS"], 25.0)
        self.assertEqual(constants["MAX_REQUEST_SECONDS"], 15.0)
        byte_limit = next(
            n.value for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "MAX_RESPONSE_BYTES" for t in n.targets)
        )
        self.assertEqual(ast.unparse(byte_limit), "256 * 1024")
        self.assertEqual(constants["BASIC_INFO_REFRESH_SECONDS"], 300.0)
        self.assertIn("25 с", PROTO)
        self.assertIn("15 с", PROTO)
        self.assertIn("256 КиБ", PROTO)
        self.assertIn("300 с", PROTO)

    def test_observed_fixture_shape_and_scaling(self):
        x = FIXTURE
        self.assertEqual((x["Type"], x["SubType"]), (112, 7250))
        self.assertEqual(x["BMSpara"][0][0], 1)
        self.assertEqual(len(x["BatcelList"]), 2)
        self.assertEqual(len(x["BatcelList"][0]), 16)
        self.assertTrue(all(2000 <= v <= 4500 for v in x["BatcelList"][0]))
        self.assertTrue(all(v == 65535 for v in x["BatcelList"][1]))
        self.assertEqual(x["Batt"][0][0] / 1000, 57.5)
        self.assertEqual(x["Batt"][1][0] / 10, -0.1)
        self.assertEqual(x["Batsoc"][0][0] / 100, 100)
        self.assertEqual(x["Batsoc"][0][2] / 1000, 250)
        self.assertEqual((x["Bstate"], x["Estate"]), (832, 832))
        for value in ("205", "250", "65535", "832", "112 / 7250"):
            self.assertIn(value, MODELS)

    def test_unknown_model_does_not_match_fla_profile_by_codes_alone(self):
        self.assertIn('MODEL = "FLA48200-P"', PROFILE_SOURCE)
        self.assertIn('configured_model.strip().upper() != MODEL', PROFILE_SOURCE)
        self.assertIn("LUX-E-48250LG03", MODELS)
        self.assertIn("112 / 7250", MODELS)
        self.assertIn("FLA48200-P", PROTO)

    def test_no_private_device_identifiers_written_into_documents(self):
        for doc in (PROTO, MODELS):
            self.assertNotIn("DevSN\":", doc)
            self.assertNotIn("wifiSN\":", doc)
            self.assertNotRegex(doc, re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"))
            self.assertNotRegex(doc, re.compile(r"\b\d{15,24}\b"))
            self.assertNotRegex(doc, re.compile(r"sk-[A-Za-z0-9_-]{20,}"))

    def test_readme_links_to_both_documents(self):
        self.assertIn("docs/P3_2_LOCAL_TCP_PROTOCOL.md", README)
        self.assertIn("docs/P3_2_MODEL_EVIDENCE.md", README)

    def test_documented_drift_comparison_matches_existing_sensor(self):
        source = (COMP / "binary_sensor.py").read_text(encoding="utf8")
        self.assertIn("return drift_v > CELL_DRIFT_HIGH_THRESHOLD_V", source)
        self.assertIn("`drift > 0,030 В`", PROTO)
        self.assertIn("≤0,020 В", PROTO)

    def test_doc_distinguishes_unverified_modes(self):
        self.assertIn("ready_for_cutover=false", PROTO)
        self.assertIn("Bstate", PROTO)
        self.assertIn("Estate", PROTO)
        self.assertIn("9152", PROTO)
        self.assertIn("не подтверждены", PROTO)
        self.assertIn("для FLA48200-P", MODELS)

if __name__ == "__main__":
    unittest.main()
