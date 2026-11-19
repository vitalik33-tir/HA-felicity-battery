"""P2.3 reconnection tests using local fake TCP, never real battery control."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import unittest

from test_api import api

REAL_CMD = b"wifilocalMonitor:get dev real infor"
BASIC_CMD = b"wifilocalMonitor:get dev basice infor"
SETTINGS_CMD = b"wifilocalMonitor:get dev set infor"
DATE_CMD = b"wifilocalMonitor:get Date"


class FakeBatteryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def fake_server(self, handler):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            yield api.FelicityClient("127.0.0.1", port)
        finally:
            server.close()
            await server.wait_closed()

    async def test_outage_multiple_poll_cycles_then_first_success_recovers(self):
        state = {"up": False, "raw_voltage": 57400, "commands": []}

        async def handler(reader, writer):
            cmd = await reader.read(128)
            state["commands"].append(cmd)
            try:
                if not state["up"]:
                    return  # simulates remote peer closing without response
                if cmd == REAL_CMD:
                    response = (
                        '{"Batt":[[%s],[-1],[null]],"Batsoc":[[10000,1000,250000]]}'
                        % state["raw_voltage"]
                    ).encode()
                elif cmd == BASIC_CMD:
                    response = b'{"Type":112}'
                elif cmd == SETTINGS_CMD:
                    response = b'{"cVolHi":3650}'
                else:
                    response = b'{"rssi":-70}'
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.fake_server(handler) as client:
            for _ in range(3):
                with self.assertRaises(api.FelicityApiError):
                    await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(state["commands"], [REAL_CMD] * 9)

            state["up"] = True
            first = await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(first["Batt"][0][0], 57400)
            self.assertEqual(first["_date"]["rssi"], -70)

            # A normal subsequent poll must be entirely fresh, not a
            # replay of the previously successful coordinator data.
            state["raw_voltage"] = 57500
            second = await api.async_get_data_bounded(client, timeout=3)
            self.assertEqual(second["Batt"][0][0], 57500)
            self.assertEqual(len(state["commands"]), 9 + 4 + 4)

    async def test_no_accumulation_of_cancelled_cycles(self):
        class HangingClient:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.cancelled = 0

            async def async_get_data(self):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    await asyncio.Event().wait()  # never completes
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
                finally:
                    self.active -= 1

        client = HangingClient()
        for _ in range(8):
            with self.assertRaisesRegex(api.FelicityApiError, "polling cycle"):
                await api.async_get_data_bounded(client, timeout=0.015)
            self.assertEqual(client.active, 0)
        self.assertEqual(client.cancelled, 8)
        self.assertEqual(client.max_active, 1)

    async def test_cancelled_optional_request_closes_tcp_then_recovers(self):
        state = {"stall_optional": True}
        cancelled_socket_closed = asyncio.Event()

        async def handler(reader, writer):
            cmd = await reader.read(128)
            try:
                if cmd == REAL_CMD:
                    writer.write(
                        b'{"Batt":[[57500],[-1],[null]],"Batsoc":[[10000,1000,250000]]}'
                    )
                    await writer.drain()
                elif state["stall_optional"] and cmd == BASIC_CMD:
                    # The client is waiting for bytes. Confirm that it
                    # closes the socket on poll timeout, without a hang.
                    remaining = await asyncio.wait_for(reader.read(128), timeout=1)
                    self.assertEqual(remaining, b"")
                    cancelled_socket_closed.set()
                elif cmd == BASIC_CMD:
                    writer.write(b'{"Type":112}')
                    await writer.drain()
                elif cmd == SETTINGS_CMD:
                    writer.write(b'{"cVolHi":3650}')
                    await writer.drain()
                else:
                    writer.write(b'{"rssi":-71}')
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with self.fake_server(handler) as client:
            with self.assertRaisesRegex(api.FelicityApiError, "polling cycle"):
                await api.async_get_data_bounded(client, timeout=0.20)
            await asyncio.wait_for(cancelled_socket_closed.wait(), timeout=1)
            state["stall_optional"] = False
            restored = await api.async_get_data_bounded(client, timeout=3)
        self.assertEqual(restored["Batt"][0][0], 57500)
        self.assertEqual(restored["_settings"]["cVolHi"], 3650)
        self.assertEqual(restored["_date"]["rssi"], -71)

    async def test_bounded_poll_does_not_alter_normal_data(self):
        sample = {"Batt": [[57500], [-1], [None]], "Estate": 832}
        mock = AsyncMock(return_value=sample)
        client = type("Client", (), {"async_get_data": mock})()
        result = await api.async_get_data_bounded(client, timeout=0.5)
        self.assertIs(result, sample)
        mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
