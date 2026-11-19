"""P2.5 end-to-end read-only battery regression on a local TCP fixture.

Tests the actual transport, runtime parser, model validation, P1 shadow
decoder and P2.4 TTL as one path, including outage and recovery.
No command is sent to the physical battery.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import unittest

from test_api import api
from test_profiles import profiles, sample
from test_validation_p2 import validation

REAL = b"wifilocalMonitor:get dev real infor"
BASIC = b"wifilocalMonitor:get dev basice infor"
SETTINGS = b"wifilocalMonitor:get dev set infor"
DATE = b"wifilocalMonitor:get Date"


class EndToEndBatteryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_pipeline_recovers_without_stale_data_or_lost_entities(self):
        observed = Counter()
        state = {
            "up": True, "current": -1, "version": "2.10", "rssi": -70
        }
        realtime = {
            key: deepcopy(value) for key, value in sample().items()
            if not key.startswith("_")
        }
        settings = sample()["_settings"]

        async def server_handler(reader, writer):
            command = await reader.read(128)
            observed[command] += 1
            try:
                if not state["up"]:
                    return
                if command == REAL:
                    realtime["Batt"][1][0] = state["current"]
                    response = json.dumps(realtime, separators=(",", ":"))
                elif command == BASIC:
                    response = json.dumps({
                        "version": state["version"],
                        "Type": 112, "SubType": 7250
                    }, separators=(",", ":"))
                elif command == SETTINGS:
                    response = (
                        json.dumps({"cVolHi": settings["cVolHi"]})
                        + json.dumps({"wCVP80": settings["wCVP80"]})
                    )
                elif command == DATE:
                    response = json.dumps({"rssi": state["rssi"]})
                else:
                    self.fail(f"Unknown command {command!r}")
                writer.write(response.encode("ascii"))
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        def runtime_validate(data):
            result = validation.validate_runtime(
                data, configured_model="FLA48200-P"
            )
            if not result.valid:
                raise api.FelicityApiError(result.reason)

        def make_client():
            return api.FelicityClient(
                "127.0.0.1", port,
                runtime_validator=runtime_validate,
                basic_info_refresh_seconds=api.BASIC_INFO_REFRESH_SECONDS,
            )

        try:
            client = make_client()
            # Initial poll must always query all four sources.
            first = await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(
                dict(observed), {REAL: 1, BASIC: 1, SETTINGS: 1, DATE: 1}
            )
            self.assertEqual(first["_basic"]["version"], "2.10")
            self.assertEqual(first["_settings"]["cVolHi"], settings["cVolHi"])
            self.assertEqual(first["_settings"]["wCVP80"], settings["wCVP80"])
            self.assertEqual(first["_date"]["rssi"], -70)
            profile = profiles.decode_fla48200p(
                first, configured_model="FLA48200-P"
            )
            self.assertTrue(profile.valid)
            self.assertEqual(profile.values["soc"], 100.0)
            self.assertEqual(profile.values["state"], "Ожидание")
            self.assertEqual(profile.values["current"], -0.1)
            self.assertEqual(
                len([k for k in profile.values if k.startswith("cell_") and k.endswith("_v")]),
                16,
            )

            # Fresh current, status and settings every cycle; basic cached.
            state["current"] = 2
            state["rssi"] = -62
            realtime["Bwarn"] = 1
            second = await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(second["Batt"][1][0], 2)
            self.assertEqual(second["Bwarn"], 1)
            self.assertEqual(second["_date"]["rssi"], -62)
            self.assertEqual(second["_basic"]["version"], "2.10")
            self.assertEqual(observed[BASIC], 1)
            self.assertEqual(observed[SETTINGS], 2)
            self.assertEqual(observed[DATE], 2)

            # Return to healthy state before the simulated network outage.
            realtime["Bwarn"] = 0
            state["up"] = False
            with self.assertRaises(api.FelicityApiError):
                await api.async_get_data_bounded(client, timeout=3)
            self.assertIsNone(client._basic_info_cache)

            state["up"] = True
            state["current"] = -3
            state["version"] = "2.11"
            state["rssi"] = -68
            recovered = await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(recovered["Batt"][1][0], -3)
            self.assertEqual(recovered["_basic"]["version"], "2.11")
            self.assertEqual(recovered["Bwarn"], 0)
            self.assertEqual(recovered["_date"]["rssi"], -68)
            self.assertEqual(observed[BASIC], 2)
            self.assertTrue(
                profiles.decode_fla48200p(
                    recovered, configured_model="FLA48200-P"
                ).valid
            )

            # Fresh process/startup also reads the actual firmware, no reuse
            # of the prior client's cache survives creating a new client.
            state["version"] = "2.12"
            restarted = make_client()
            after_restart = await api.async_get_data_bounded(restarted, timeout=3)
            self.assertEqual(after_restart["_basic"]["version"], "2.12")
            self.assertEqual(observed[BASIC], 3)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
