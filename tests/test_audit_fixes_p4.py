"""Regression tests from the 19-Nov full audit: faults, validation, optional TCP.

No physical BMS control, no Home Assistant restart. Test against the
model's anonymized real capture plus disposable loopback TCP server.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from contextlib import asynccontextmanager
import unittest

from test_api import api
from test_validation_p2 import validation, sample


class FaultTelemetryTests(unittest.TestCase):
    def clean(self, packet):
        return validation.sanitize_runtime(packet, configured_model="FLA48200-P")

    def test_all_hardware_alarm_registers_survive_bad_voltage_and_soc(self):
        for register in ("Bfault","Bwarn","BBfault","BBwarn"):
            with self.subTest(register=register):
                frame = sample()
                frame[register] = 4
                frame["Batt"][0][0] = "INVALID"
                frame["Batsoc"][0][0] = 999999
                out = self.clean(frame)
                self.assertEqual(out[register], 4)
                self.assertIsNone(out["Batt"][0][0])
                self.assertIsNone(out["Batsoc"][0][0])
                self.assertEqual(frame["Batt"][0][0],"INVALID")
                self.assertFalse(validation.validate_runtime(out,configured_model="FLA48200-P").valid)
                self.assertTrue(validation.has_hardware_alarm(out))

    def test_alarm_with_mismatched_identity_masks_all_measurements(self):
        frame = sample()
        frame["Bwarn"] = 4
        frame["Type"] = 999
        out = self.clean(frame)
        self.assertEqual(out["Bwarn"],4)
        self.assertIsNone(out["Batt"])
        self.assertIsNone(out["Batsoc"])
        self.assertIsNone(out["Estate"])
        self.assertIsNone(out["BMaxMin"])
        self.assertIsNone(out["BatcelList"])

    def test_impossible_optional_soh_temperature_and_capacity_become_unknown(self):
        frame = sample()
        frame["Batsoc"][0][1] = 65535
        frame["Batsoc"][0][2] = 99999999
        frame["BTemp"][0][0] = 65535
        frame["BTemp"][0][1] = 32767
        out = self.clean(frame)
        self.assertEqual(out["Batsoc"][0][0],10000)
        self.assertEqual(out["Batt"][0][0],57500)
        self.assertIsNone(out["Batsoc"][0][1])
        self.assertIsNone(out["Batsoc"][0][2])
        self.assertIsNone(out["BTemp"][0][0])
        self.assertIsNone(out["BTemp"][0][1])
        self.assertTrue(validation.validate_runtime(out,configured_model="FLA48200-P").valid)

    def test_bad_cell_does_not_leak_as_pack_voltage(self):
        frame = sample()
        frame["Bwarn"] = 1
        frame["BatcelList"][0][0] = 1800
        out = self.clean(frame)
        self.assertEqual(out["Bwarn"],1)
        self.assertIsNone(out["BatcelList"])
        self.assertIsNone(out["BMaxMin"])
        self.assertEqual(out["Batt"][0][0],57500)

    def test_invalid_optional_limit_does_not_block_main_voltages(self):
        frame=sample()
        frame["LVolCur"][1][1]=65535
        out=self.clean(frame)
        self.assertIsNone(out["LVolCur"][1][1])
        self.assertEqual(out["Batt"][0][0],57500)
        self.assertTrue(validation.validate_runtime(out, configured_model="FLA48200-P").valid)

    def test_current_minus_one_remains_minus_point_one_amp(self):
        out=self.clean(sample())
        self.assertEqual(out["Batt"][1][0],-1)
        self.assertTrue(validation.validate_runtime(out,configured_model="FLA48200-P").valid)

    def test_no_alarm_bad_required_core_still_fails(self):
        frame=sample()
        frame["Batt"][0][0]=0
        clean=self.clean(frame)
        self.assertFalse(validation.validate_runtime(clean,configured_model="FLA48200-P").valid)

    def test_other_models_unchanged(self):
        packet={"Batsoc":[[999999, 65535, 99999999]],"Batt":[[0],["oops"]]}
        out=validation.sanitize_runtime(packet,configured_model="LUX-X-96050HG01")
        self.assertEqual(out,packet)


class OptionalTimeoutTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def server(self, handler):
        server=await asyncio.start_server(handler,"127.0.0.1",0)
        try:
            yield api.FelicityClient("127.0.0.1",server.sockets[0].getsockname()[1])
        finally:
            server.close()
            await server.wait_closed()

    async def test_all_optional_requests_hang_but_realtime_remains_available(self):
        seen=[]
        realtime={k:v for k,v in sample().items() if not k.startswith("_")}
        import json
        async def handler(reader,writer):
            command=await reader.read(128)
            seen.append(command)
            try:
                if command==b"wifilocalMonitor:get dev real infor":
                    writer.write(json.dumps(realtime).encode())
                    await writer.drain()
                else:
                    await reader.read(1) # waits for client to cancel & close
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.server(handler) as c:
            from unittest.mock import patch
            c._enforce_optional_budget = True
            with patch.object(api,"OPTIONAL_REQUEST_SECONDS",0.15):
                t=asyncio.get_running_loop().time()
                data=await api.async_get_data_bounded(c,timeout=4.0)
                elapsed=asyncio.get_running_loop().time()-t
        self.assertLess(elapsed,3.0)
        self.assertEqual(data["Batt"][0][0],57500)
        self.assertNotIn("_basic",data)
        self.assertNotIn("_settings",data)
        self.assertNotIn("_date",data)
        self.assertEqual(len(seen),4)

    async def test_end_to_end_alarm_corrupt_voltage_survives_client_parser(self):
        import json
        frame = {k: v for k, v in sample().items() if not k.startswith("_")}
        frame["Bfault"] = 4
        frame["Batt"][0][0] = 0
        frame["Batsoc"][0][1] = 65535
        observed = []

        async def handler(reader, writer):
            cmd = await reader.read(128)
            observed.append(cmd)
            try:
                if b"real infor" in cmd:
                    writer.write(json.dumps(frame).encode())
                elif b"basice infor" in cmd:
                    writer.write(b'{"version":"2.10"}')
                elif b"set infor" in cmd:
                    writer.write(b'{"cVolHi":3650}')
                elif b"get Date" in cmd:
                    writer.write(b'{"rssi":-70}')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        def model_validate(data):
            cleaned = validation.sanitize_runtime(data, configured_model="FLA48200-P")
            result = validation.validate_runtime(cleaned, configured_model="FLA48200-P")
            if not result.valid and not validation.has_hardware_alarm(cleaned):
                raise api.FelicityApiError(result.reason)
            data.clear()
            data.update(cleaned)

        async with self.server(handler) as base:
            client = api.FelicityClient(
                "127.0.0.1", base._port,
                runtime_validator=model_validate,
                preserve_alarm_frames=True,
                enforce_optional_budget=True,
            )
            result = await api.async_get_data_bounded(client, timeout=4)
        self.assertEqual(result["Bfault"], 4)
        self.assertEqual(result["Bwarn"], 0)
        self.assertIsNone(result["Batt"][0][0])
        self.assertIsNone(result["Batsoc"][0][1])
        self.assertEqual(result["_date"]["rssi"], -70)
        self.assertEqual(len(observed), 4)

    async def test_alarm_only_without_batt_or_soc_retains_fault(self):
        def model_validate(data):
            cleaned = validation.sanitize_runtime(data, configured_model="FLA48200-P")
            if (
                not validation.validate_runtime(cleaned, configured_model="FLA48200-P").valid
                and not validation.has_hardware_alarm(cleaned)
            ):
                raise api.FelicityApiError("invalid model alarm")
            data.clear()
            data.update(cleaned)

        c = api.FelicityClient(
            "127.0.0.1", 53970, runtime_validator=model_validate,
            preserve_alarm_frames=True,
            enforce_optional_budget=True,
        )
        c._async_read_raw_with_retry = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(
            side_effect=[
                '{"Type":112,"SubType":7250,"Bfault":4,"Bwarn":0}',
                '{"Type":112}', '{"cVolHi":3650}', '{"rssi":-70}',
            ]
        )
        result = await c.async_get_data()
        self.assertEqual(result["Bfault"], 4)
        self.assertNotIn("Batt", result)
        self.assertNotIn("Batsoc", result)

    async def test_first_optional_hangs_but_later_options_still_polled(self):
        from unittest.mock import AsyncMock,patch
        c=api.FelicityClient(
            "127.0.0.1",53970,enforce_optional_budget=True
        )
        async def response(cmd):
            if b"real infor" in cmd:
                return '{"Batt":[[57500],[-1],[null]],"Batsoc":[[10000,1000,250000]]}'
            if b"basice infor" in cmd:
                await asyncio.Event().wait()
            if b"set infor" in cmd:
                return '{"cVolHi":3650}'
            if b"get Date" in cmd:
                return '{"rssi":-70}'
            raise AssertionError(cmd)
        c._async_read_raw_with_retry=AsyncMock(side_effect=response)
        with patch.object(api,"OPTIONAL_REQUEST_SECONDS",0.025):
            data=await api.async_get_data_bounded(c,timeout=1.0)
        self.assertIn("_settings",data)
        self.assertIn("_date",data)
        self.assertNotIn("_basic",data)
        self.assertEqual(data["Batt"][0][0],57500)
        self.assertEqual(c._async_read_raw_with_retry.await_count,4)


if __name__=="__main__":
    unittest.main()
