"""P2.4 read-only polling optimization regression tests.

Only FLA48200-P static basic-info is eligible for 300-second caching.
Runtime, settings and date remain real TCP reads on every 30-second poll.
"""

from __future__ import annotations

import unittest
from collections import Counter
from unittest.mock import AsyncMock

from test_api import api

REAL = b"wifilocalMonitor:get dev real infor"
BASIC = b"wifilocalMonitor:get dev basice infor"
SETTINGS = b"wifilocalMonitor:get dev set infor"
DATE = b"wifilocalMonitor:get Date"

REAL_FRAME = '{"Batt":[[57500],[-1],[null]],"Batsoc":[[10000,1000,250000]],"Estate":832,"Bstate":832}'
SETTINGS_FRAME = '{"cVolHi":3650}{"bDCHi2":1500}'
DATE_FRAME = '{"rssi":-72,"dateTime":"2026-11-19 18:00:00"}'


class BasicInfoCacheTests(unittest.IsolatedAsyncioTestCase):
    def init_client(self, refresh_seconds):
        client = api.FelicityClient(
            "127.0.0.1", 53970, basic_info_refresh_seconds=refresh_seconds
        )
        self.counters = Counter()
        self.version = "v1"
        self.rssi = -72
        self.settings_limit = 3650
        self.real_current = -1
        self.fail_basic = False
        self.fail_real = False

        async def reply(cmd):
            self.counters[cmd] += 1
            if cmd == REAL:
                if self.fail_real:
                    raise api.FelicityApiError("simulated lost realtime connection")
                return REAL_FRAME.replace("[-1]", f"[{self.real_current}]")
            if cmd == BASIC:
                if self.fail_basic:
                    raise api.FelicityApiError("simulated missing static info")
                return '{"version":"' + self.version + '","Type":112,"SubType":7250}'
            if cmd == SETTINGS:
                return SETTINGS_FRAME.replace("3650", str(self.settings_limit))
            if cmd == DATE:
                return DATE_FRAME.replace("-72", str(self.rssi))
            self.fail("unexpected command")

        client._async_read_raw_with_retry = AsyncMock(side_effect=reply)
        return client

    async def test_initial_query_then_three_live_requests_every_poll(self):
        c = self.init_client(300)
        first = await c.async_get_data()
        self.assertEqual(first["_basic"]["version"], "v1")
        self.assertEqual(dict(self.counters), {
            REAL:1, BASIC:1, SETTINGS:1, DATE:1
        })

        self.version = "v2"
        self.rssi = -55
        self.settings_limit = 3600
        self.real_current = 2
        second = await c.async_get_data()
        self.assertEqual(second["_basic"]["version"], "v1")
        self.assertEqual(second["_date"]["rssi"], -55)
        self.assertEqual(second["_settings"]["cVolHi"], 3600)
        self.assertEqual(second["Batt"][1][0], 2)
        self.assertEqual(self.counters[REAL], 2)
        self.assertEqual(self.counters[SETTINGS], 2)
        self.assertEqual(self.counters[DATE], 2)
        self.assertEqual(self.counters[BASIC], 1)
        # Each coordinator frame must hold its own snapshot.
        second["_basic"]["version"] = "changed_by_consumer"
        third = await c.async_get_data()
        self.assertEqual(third["_basic"]["version"], "v1")

    async def test_cache_expires_and_firmware_version_refreshes(self):
        c = self.init_client(300)
        await c.async_get_data()
        self.version = "v2"
        c._basic_info_cache_at -= 301  # artificial five-minute elapsed
        after = await c.async_get_data()
        self.assertEqual(after["_basic"]["version"], "v2")
        self.assertEqual(self.counters[BASIC], 2)

    async def test_default_unknown_model_retains_four_request_cadence(self):
        c = self.init_client(None)
        for n in range(3):
            await c.async_get_data()
        self.assertEqual(dict(self.counters), {
            REAL:3, BASIC:3, SETTINGS:3, DATE:3
        })

    async def test_missing_basic_at_startup_is_retried_next_poll(self):
        c = self.init_client(300)
        self.fail_basic = True
        first = await c.async_get_data()
        self.assertNotIn("_basic", first)
        self.fail_basic = False
        second = await c.async_get_data()
        self.assertEqual(second["_basic"]["version"], "v1")
        self.assertEqual(self.counters[BASIC], 2)

    async def test_expired_basic_query_failure_never_serves_stale_snapshot(self):
        c = self.init_client(300)
        await c.async_get_data()
        c._basic_info_cache_at -= 301
        self.fail_basic = True
        failed = await c.async_get_data()
        self.assertNotIn("_basic", failed)
        self.fail_basic = False
        recovered = await c.async_get_data()
        self.assertEqual(recovered["_basic"]["version"], "v1")
        self.assertEqual(self.counters[BASIC], 3)

    async def test_realtime_outage_invalidates_basic_cache(self):
        c = self.init_client(300)
        await api.async_get_data_bounded(c)
        self.fail_real = True
        with self.assertRaises(api.FelicityApiError):
            await api.async_get_data_bounded(c, timeout=1.0)
        self.assertIsNone(c._basic_info_cache)
        self.fail_real = False
        self.version = "v3"
        restored = await api.async_get_data_bounded(c)
        self.assertEqual(restored["_basic"]["version"], "v3")
        self.assertEqual(self.counters[BASIC], 2)

    async def test_cycle_timeout_invalidates_basic_cache(self):
        c = self.init_client(300)
        await c.async_get_data()
        async def hung_poll():
            import asyncio
            await asyncio.Event().wait()
        c.async_get_data = hung_poll
        with self.assertRaisesRegex(api.FelicityApiError, "polling cycle"):
            await api.async_get_data_bounded(c, timeout=0.015)
        self.assertIsNone(c._basic_info_cache)

    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            api.FelicityClient("127.0.0.1", 53970, basic_info_refresh_seconds=0)


if __name__ == "__main__":
    unittest.main()
