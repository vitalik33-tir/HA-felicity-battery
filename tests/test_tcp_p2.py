"""P2 TCP transport regression tests against a disposable in-process server.

The real battery/inverter never receives commands from these tests.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch
import unittest

from test_api import api


class FakeFelicityTcpTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def server(self, handler):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            yield api.FelicityClient("127.0.0.1", port)
        finally:
            server.close()
            await server.wait_closed()

    async def test_fragmented_multi_json_response_roundtrip(self):
        first = b'{"wCVP80": 3350}'
        second = b'{"wCVP20": 3150, "cVolHi": 3650}'

        async def handler(reader, writer):
            try:
                cmd = await reader.read(128)
                self.assertEqual(cmd, b"wifilocalMonitor:get dev set infor")
                writer.write(first[:9])
                await writer.drain()
                await asyncio.sleep(0.025)
                writer.write(first[9:] + second[:12])
                await writer.drain()
                await asyncio.sleep(0.025)
                writer.write(second[12:])
                await writer.drain()
                await asyncio.sleep(0.025)
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.server(handler) as client:
            raw = await client._async_read_raw(b"wifilocalMonitor:get dev set infor")
            objects = api.parse_json_objects(raw)
        self.assertEqual(objects, [{"wCVP80":3350},{"wCVP20":3150,"cVolHi":3650}])

    async def test_remote_close_before_any_data_raises(self):
        async def handler(reader, writer):
            await reader.read(128)
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            with self.assertRaisesRegex(api.FelicityApiError, "No data received"):
                await client._async_read_raw(b"wifilocalMonitor:get dev real infor")

    async def test_first_byte_timeout_raises_not_empty_success(self):
        async def handler(reader, writer):
            await reader.read(128)
            try:
                await asyncio.sleep(0.15)
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.server(handler) as client:
            with patch.object(api, "FIRST_BYTE_TIMEOUT", 0.03):
                with self.assertRaisesRegex(api.FelicityApiError, "No data received"):
                    await client._async_read_raw(b"wifilocalMonitor:get dev real infor")

    async def test_retry_after_forced_close_recovers(self):
        attempts = 0

        async def handler(reader, writer):
            nonlocal attempts
            await reader.read(128)
            attempts += 1
            try:
                if attempts == 2:
                    writer.write(b'{"Batt": [[57500], [-1], [null]], "Batsoc": [[10000,1000,250000]]}')
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.server(handler) as client:
            raw = await client._async_read_raw_with_retry(
                b"wifilocalMonitor:get dev real infor", retries=1, backoff=0
            )
            parsed = client._parse_real_payload(raw)
        self.assertEqual(attempts, 2)
        self.assertEqual(parsed["Batt"][1][0], -1)

    async def test_incomplete_last_json_object_is_retried(self):
        attempts = 0
        valid = b'{"Batt": [[57500], [-1], [null]], "Batsoc": [[10000,1000,250000]]}'

        async def handler(reader, writer):
            nonlocal attempts
            await reader.read(128)
            attempts += 1
            if attempts == 1:
                writer.write(b'{"Batsoc":[[10000,1000,250000]]}{"Batt":[[')
            else:
                writer.write(valid)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            result = await client._async_read_raw_with_retry(
                b"wifilocalMonitor:get dev real infor", retries=1, backoff=0
            )
        self.assertEqual(attempts, 2)
        self.assertEqual(result.encode(), valid)

    async def test_four_command_cycle_preserves_all_sources(self):
        responses = {
            b"wifilocalMonitor:get dev real infor":
                b'{"Batt": [[57500], [-1], [null]], "Batsoc": [[10000,1000,250000]], "Bstate": 832, "Estate": 832}',
            b"wifilocalMonitor:get dev basice infor":
                b'{"Type": 112, "SubType": 7250, "version": "2.10"}',
            b"wifilocalMonitor:get dev set infor":
                b'{"wCVP80":3350}{"cVolHi":3650}',
            b"wifilocalMonitor:get Date":
                b'{"rssi": -72, "tick": 120}',
        }
        received = []

        async def handler(reader, writer):
            cmd = await reader.read(128)
            received.append(cmd)
            writer.write(responses[cmd])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            result = await client.async_get_data()
        self.assertEqual(len(received), 4)
        self.assertEqual(result["Batt"][1][0], -1)
        self.assertEqual(result["Estate"], 832)
        self.assertEqual(result["_basic"]["SubType"], 7250)
        self.assertEqual(result["_settings"]["wCVP80"], 3350)
        self.assertEqual(result["_settings"]["cVolHi"], 3650)
        self.assertEqual(result["_date"]["rssi"], -72)

    async def test_optional_settings_outage_keeps_valid_realtime(self):
        attempts = {}

        async def handler(reader, writer):
            cmd = await reader.read(128)
            attempts[cmd] = attempts.get(cmd, 0) + 1
            if cmd == b"wifilocalMonitor:get dev real infor":
                writer.write(b'{"Batt": [[57500], [-1], [null]], "Batsoc": [[10000,1000,250000]]}')
            elif cmd == b"wifilocalMonitor:get dev basice infor":
                writer.write(b'{"Type":112}')
            elif cmd == b"wifilocalMonitor:get Date":
                writer.write(b'{"rssi":-72}')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            # No sleeps in the fake server; still exercise all retries.
            with patch.object(api, "READ_RETRY_BACKOFF", 0.0):
                result = await client.async_get_data()
        self.assertEqual(result["Batt"][0][0], 57500)
        self.assertNotIn("_settings", result)
        self.assertEqual(result["_date"]["rssi"], -72)
        self.assertEqual(attempts[b"wifilocalMonitor:get dev set infor"], 3)

    async def test_realtime_failure_cannot_be_reported_as_success(self):
        attempts = 0

        async def handler(reader, writer):
            nonlocal attempts
            await reader.read(128)
            attempts += 1
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            with patch.object(api, "READ_RETRY_BACKOFF", 0.0):
                with self.assertRaises(api.FelicityApiError):
                    await client.async_get_data()
        self.assertEqual(attempts, 3)

    async def test_response_exceeding_limit_is_rejected(self):
        async def handler(reader, writer):
            await reader.read(128)
            writer.write(b'{"junk":"' + b"x" * 1024 + b'"}')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with self.server(handler) as client:
            with patch.object(api, "MAX_RESPONSE_BYTES", 256):
                with self.assertRaisesRegex(api.FelicityApiError, "size"):
                    await client._async_read_raw(b"wifilocalMonitor:get dev real infor")

    async def test_continuous_trickle_cannot_hold_socket_forever(self):
        async def handler(reader, writer):
            await reader.read(128)
            try:
                for i in range(30):
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.03)
            except (ConnectionError, BrokenPipeError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        async with self.server(handler) as client:
            with patch.object(api, "MAX_REQUEST_SECONDS", 0.2):
                with self.assertRaisesRegex(api.FelicityApiError, "deadline"):
                    await client._async_read_raw(b"wifilocalMonitor:get dev real infor")


if __name__ == "__main__":
    unittest.main()
